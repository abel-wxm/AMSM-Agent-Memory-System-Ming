"""图谱引擎 — 节点/关系/冲突检测。

负责：
  - 分析新片段，提取实体 → 写入 graph_nodes
  - 建立新节点与已有节点的关系 → 写入 graph_edges
  - 检测新事实与已有节点冲突 → superseded 标记
  - 所有变更同步写入 mutation_log（只 INSERT，H-4）
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from memoir.db.conn import get_connection
from memoir.config_params import MSD_SUMMARIZE_INTERVAL_FRAGMENTS, MSD_SUMMARIZE_INTERVAL_DAYS

logger = logging.getLogger(__name__)


def _call_llm_for_graph_extraction(
    summary: str,
    keywords: List[str],
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """提取实体和关系。"""
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        return {"entities": [{"name": summary[:10], "type": "fact"}], "relations": []}

    endpoint = f"{base_url}/chat/completions"
    model = config.get("model", "")
    api_key = config.get("api_key", "")
    timeout = config.get("timeout", 120)

    prompt = (
        "请从以下摘要和关键词中提取实体及实体间的关系。\n"
        "实体类型只能是: person, place, fact, concept, skill\n"
        "关系类型只能是: related_to, belongs_to, works_at, knows, lives_in\n\n"
        "以 JSON 格式输出，包含 `entities` 数组和 `relations` 数组。\n"
        f"摘要：{summary}\n关键词：{keywords}"
    )

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个严谨的图谱提取引擎，仅输出JSON。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    req = urllib.request.Request(endpoint, data=json.dumps(data).encode("utf-8"))
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            resp_body = response.read().decode("utf-8")
            resp_data = json.loads(resp_body)
            content = resp_data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            with open("/home/abel/amsm_debug.log", "a") as f:
                f.write(f"Graph extraction LLM response: {content}\n")
            return {
                "entities": parsed.get("entities", []),
                "relations": parsed.get("relations", [])
            }
    except Exception as e:
        logger.error(f"图谱提取 LLM 请求失败: {e}")
        with open("/home/abel/amsm_debug.log", "a") as f:
            f.write(f"Graph extraction exception: {e}\n")
        return {"entities": [{"name": summary[:10], "type": "fact"}], "relations": []}


def _call_llm_for_conflict(
    new_summary: str,
    old_summaries: List[Dict[str, str]],
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """判断事实冲突。
    old_summaries 格式: [{"node_id": "xxx", "summary": "yyy"}]
    返回: {"conflicts": [{"node_id": "xxx", "reason": "zzz"}]}
    """
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        return {"conflicts": []}
    
    if not old_summaries:
        return {"conflicts": []}

    endpoint = f"{base_url}/chat/completions"
    model = config.get("model", "")
    api_key = config.get("api_key", "")
    timeout = config.get("timeout", 120)

    old_text = json.dumps(old_summaries, ensure_ascii=False)
    prompt = (
        "你是一个冲突检测引擎。现有新事实，和一批旧事实列表。\n"
        "判断新事实是否完全推翻或替代了旧事实中的某一条（例如职业变更、搬家、改变主意）。\n"
        f"新事实：{new_summary}\n"
        f"旧事实列表：{old_text}\n\n"
        "如果有冲突，请在返回的JSON中的 `conflicts` 数组列出被替代的 `node_id`，和用一句话总结的 `reason`。"
    )

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "仅输出JSON格式的检测结果。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    req = urllib.request.Request(endpoint, data=json.dumps(data).encode("utf-8"))
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            resp_body = response.read().decode("utf-8")
            resp_data = json.loads(resp_body)
            content = resp_data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return {"conflicts": parsed.get("conflicts", [])}
    except Exception as e:
        logger.error(f"冲突检测 LLM 请求失败: {e}")
        return {"conflicts": []}


def update_graph(
    fragment_id: str,
    session_id: str,
    summary: str,
    keywords: List[str],
    config: Dict[str, Any],
) -> List[str]:
    """分析片段，提取实体并更新图谱。"""
    llm_res = _call_llm_for_graph_extraction(summary, keywords, config)
    entities = llm_res.get("entities", [])
    relations = llm_res.get("relations", [])
    
    conn = get_connection()
    new_node_ids = []
    
    # entity_name 到 node_id 的映射，方便后面建边
    name_to_id = {}
    
    now = int(time.time())
    try:
        cursor = conn.cursor()
        
        # 1. 写入节点
        for ent in entities:
            node_id = str(uuid.uuid4())
            name = ent.get("name", "Unknown")
            etype = ent.get("type", "fact")
            
            cursor.execute(
                """
                INSERT INTO graph_nodes (
                    node_id, fragment_id, session_id, entity_name, entity_type,
                    summary, status, valid_from
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (node_id, fragment_id, session_id, name, etype, summary, now)
            )
            new_node_ids.append(node_id)
            name_to_id[name] = node_id
            
        # 2. 写入边
        valid_relation_types = {
            "supersedes", "implies_outdated", "related_to", 
            "belongs_to", "works_at", "knows", "lives_in"
        }
        for rel in relations:
            src_name = rel.get("source")
            tgt_name = rel.get("target")
            rel_type = rel.get("type", "related_to")
            if rel_type not in valid_relation_types:
                rel_type = "related_to"
            affinity = float(rel.get("affinity", 0.5))
            
            src_id = name_to_id.get(src_name)
            tgt_id = name_to_id.get(tgt_name)
            
            # 这里简化处理：只能在本次提取的实体内建边。跨片段关系后续补充。
            if src_id and tgt_id:
                edge_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO graph_edges (
                        edge_id, from_node_id, to_node_id, relation_type,
                        affinity, created_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active')
                    """,
                    (edge_id, src_id, tgt_id, rel_type, affinity, now)
                )
                
        conn.commit()
    except Exception as e:
        logger.error(f"图谱落库失败: {e}")
        conn.rollback()
    finally:
        conn.close()

    # 3. 对新节点进行冲突检测
    for nid in new_node_ids:
        detect_conflict(nid, session_id, config)

    # 4. 检查是否触发总MSD独立提炼
    _check_and_summarize_session(session_id, config)

    return new_node_ids


def detect_conflict(
    new_node_id: str,
    session_id: str,
    config: Dict[str, Any],
) -> bool:
    """检测新节点是否与已有节点存在事实冲突。"""
    conn = get_connection()
    conflict_handled = False
    
    try:
        cursor = conn.cursor()
        
        # 获取新节点信息
        cursor.execute("SELECT summary, fragment_id FROM graph_nodes WHERE node_id = ?", (new_node_id,))
        new_row = cursor.fetchone()
        if not new_row:
            return False
            
        new_summary = new_row["summary"]
        new_fragment_id = new_row["fragment_id"]
        now = int(time.time())
        
        # 找出当前会话的其它 active 节点
        cursor.execute(
            "SELECT node_id, summary FROM graph_nodes WHERE session_id = ? AND status = 'active' AND node_id != ?",
            (session_id, new_node_id)
        )
        active_nodes = cursor.fetchall()
        if not active_nodes:
            return False
            
        old_summaries = [{"node_id": r["node_id"], "summary": r["summary"]} for r in active_nodes]
        
        # 调用大模型判定
        res = _call_llm_for_conflict(new_summary, old_summaries, config)
        conflicts = res.get("conflicts", [])
        
        if conflicts:
            conflict_handled = True
            for c in conflicts:
                old_id = c.get("node_id")
                reason = c.get("reason", "发现事实冲突")
                
                # 1. 更新旧节点为 superseded
                trig_json = json.dumps([new_fragment_id])
                cursor.execute(
                    """
                    UPDATE graph_nodes 
                    SET status = 'superseded', valid_until = ?, superseded_at = ?, superseded_reason = ?, triggered_by_fragment_ids = ?
                    WHERE node_id = ?
                    """,
                    (now, now, reason, trig_json, old_id)
                )
                
                # 2. 建立 supersedes 边
                edge_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO graph_edges (
                        edge_id, from_node_id, to_node_id, relation_type, affinity, created_at, status
                    ) VALUES (?, ?, ?, 'supersedes', 1.0, ?, 'active')
                    """,
                    (edge_id, new_node_id, old_id, now)
                )
                
                # 3. 写入 mutation_log
                log_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO mutation_log (
                        log_id, changed_at, node_id, old_status, new_status, reason, triggered_by_fragment
                    ) VALUES (?, ?, ?, 'active', 'superseded', ?, ?)
                    """,
                    (log_id, now, old_id, reason, new_fragment_id)
                )
                
                # TODO: 级联 superseded (implies_outdated 边) 视情况扩展
                
            conn.commit()
            
    except Exception as e:
        logger.error(f"冲突检测失败: {e}")
        conn.rollback()
    finally:
        conn.close()
        
    return conflict_handled

def _call_llm_for_session_summarize(
    summaries_text: str,
    config: Dict[str, Any]
) -> Dict[str, Any]:
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        return {"summary": summaries_text[:40], "keywords": ["测试关键词"]}
        
    endpoint = f"{base_url}/chat/completions"
    model = config.get("model", "")
    api_key = config.get("api_key", "")
    timeout = config.get("timeout", 120)
    
    prompt = (
        "请为以下整个会话的片段列表提炼出全局总结（不超过50字）和3-5个全局核心关键词。\n"
        "请按话题出现的频次（多次提及的权重高）和时效性（越往后的片段为越近期的话题，权重高）进行加权提取。\n"
        "以 JSON 格式输出，包含 `summary` 和 `keywords`。\n\n"
        "片段列表：\n" + summaries_text[:8000]  # 简单截断防超长
    )
    
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个高度概括的总结引擎，仅输出JSON。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"}
    }
    
    req = urllib.request.Request(endpoint, data=json.dumps(data).encode("utf-8"))
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
        
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            resp_body = response.read().decode("utf-8")
            resp_data = json.loads(resp_body)
            content = resp_data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return {
                "summary": parsed.get("summary", ""),
                "keywords": parsed.get("keywords", [])
            }
    except Exception as e:
        logger.error(f"总MSD总结LLM请求失败: {e}")
        return {"summary": summaries_text[:40], "keywords": ["请求错误"]}

def _check_and_summarize_session(session_id: str, config: Dict[str, Any]) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # 1. 获取 sessions.created_at
        cursor.execute("SELECT created_at FROM sessions WHERE session_id = ?", (session_id,))
        s_row = cursor.fetchone()
        if not s_row:
            return
        session_created_at = s_row["created_at"]
        
        # 2. 获取 global_graph_index 记录
        cursor.execute("SELECT last_summarized_at, summarize_count FROM global_graph_index WHERE session_id = ?", (session_id,))
        g_row = cursor.fetchone()
        
        if g_row:
            last_summarized_at = g_row["last_summarized_at"]
            summarize_count = g_row["summarize_count"]
        else:
            last_summarized_at = session_created_at
            summarize_count = 0
            
        now = int(time.time())
        
        # 3. 统计新增片段数
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM memory_fragments WHERE session_id = ? AND created_at > ?", 
            (session_id, last_summarized_at)
        )
        c_row = cursor.fetchone()
        new_count = c_row["cnt"] if c_row else 0
        
        trigger = False
        
        if summarize_count == 0:
            if (now - last_summarized_at) > MSD_SUMMARIZE_INTERVAL_DAYS * 86400:
                trigger = True
            elif new_count >= MSD_SUMMARIZE_INTERVAL_FRAGMENTS:
                trigger = True
            else:
                cursor.execute(
                    "SELECT created_at FROM memory_fragments WHERE session_id = ? ORDER BY created_at DESC LIMIT 2",
                    (session_id,)
                )
                ts_rows = cursor.fetchall()
                if len(ts_rows) == 2:
                    if (ts_rows[0]["created_at"] - ts_rows[1]["created_at"]) > 1800:
                        trigger = True
        else:
            if (now - last_summarized_at) > MSD_SUMMARIZE_INTERVAL_DAYS * 86400:
                trigger = True
            elif new_count >= MSD_SUMMARIZE_INTERVAL_FRAGMENTS:
                trigger = True
                
        if not trigger:
            return
            
        # 4. 执行大模型总结
        cursor.execute(
            "SELECT summary, created_at FROM memory_fragments WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,)
        )
        all_frags = cursor.fetchall()
        if not all_frags:
            return
            
        text_lines = []
        for i, r in enumerate(all_frags):
            text_lines.append(f"片段{i+1}: {r['summary']}")
            
        summaries_text = "\n".join(text_lines)
        
        llm_res = _call_llm_for_session_summarize(summaries_text, config)
        new_summary = llm_res.get("summary", "")
        new_keywords = llm_res.get("keywords", [])
        keywords_json = json.dumps(new_keywords, ensure_ascii=False)
        
        # 5. 更新或插入
        if g_row:
            cursor.execute(
                """
                UPDATE global_graph_index 
                SET summary = ?, keywords = ?, last_summarized_at = ?, summarize_count = summarize_count + 1, updated_at = ?
                WHERE session_id = ?
                """,
                (new_summary, keywords_json, now, now, session_id)
            )
        else:
            global_node_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO global_graph_index (
                    global_node_id, session_id, summary, keywords, timestamps, created_at, updated_at, last_summarized_at, summarize_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (global_node_id, session_id, new_summary, keywords_json, "[]", now, now, now, 1)
            )
        conn.commit()
    except Exception as e:
        logger.error(f"总MSD独立提炼失败: {e}")
        conn.rollback()
    finally:
        conn.close()
