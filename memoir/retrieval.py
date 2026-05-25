"""
检索引擎 — prefetch 钩子调用。

严格按 SPEC v6.2 §五 的五阶段流程执行：
  阶段一：预处理
  阶段二：候选集构建
  阶段三：统一评分 (调用 memoir.scorer)
  阶段四：加载深度决策
  阶段五：质量检查 → 跨会话触发判断 (Mock)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.request
from collections import deque
from typing import Any, Dict, List, Set, Tuple

from memoir.db.conn import get_connection
from memoir.scorer import score_fragments
from memoir.config_params import (
    THRESHOLD_LOW,
    THRESHOLD_HIGH,
    MAX_RETURN,
    MAX_INJECT_CHARS,
    TOP_FULL_LOAD,
    GRAPH_TRAVERSE_MAX_DEPTH,
    GRAPH_TRAVERSE_MIN_AFFINITY,
    INJECT_SUPPRESS_MSG_COUNT,
    INJECT_SUPPRESS_CHARS,
    FULL_SCAN_MAX_RETURN,
    FULL_SCAN_MAX_CHARS,
)

logger = logging.getLogger(__name__)

def _tokenize_query(query: str) -> List[str]:
    import re
    stop_words = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
        "都", "一", "一个", "上", "也", "吗", "到", "说", "要", "会",
        "你", "他", "她", "它", "这", "那", "么", "什么", "怎么", "怎样",
        "吧", "呢", "啊", "哦", "嗯", "过", "着", "把", "被", "让",
        "以前", "现在", "目前", "最近", "当前", "今天", "这次",
        "前", "曾经", "当年", "那时", "去年", "上次", "之前",
    }
    parts = query.strip().split()
    result = []
    for part in parts:
        if re.search(r'[\u4e00-\u9fff]', part):
            cleaned = part
            for sw in sorted(stop_words, key=len, reverse=True):
                cleaned = cleaned.replace(sw, ' ')
            segments = [s.strip() for s in cleaned.split() if len(s.strip()) >= 2]
            for seg in segments:
                if len(seg) <= 4:
                    result.append(seg)
                else:
                    for size in [2, 3]:
                        for i in range(len(seg) - size + 1):
                            sub = seg[i:i+size]
                            if sub not in stop_words:
                                result.append(sub)
        else:
            if len(part) >= 2 and part.lower() not in stop_words:
                result.append(part)
    seen = set()
    unique = []
    for token in result:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique if unique else [query[:10]] if len(query) >= 2 else []

def _extract_json_str(text: str) -> str:
    """提取文本中的 JSON 部分，容错处理 Markdown 格式。"""
    start = text.find('{')
    if start == -1:
        start = text.find('[')
    if start == -1:
        return text
    
    end_brace = text.rfind('}')
    end_bracket = text.rfind(']')
    end = max(end_brace, end_bracket)
    
    if end != -1 and end >= start:
        return text[start:end+1]
    return text

def _call_llm_time_analysis(query: str, config: Dict[str, Any]) -> Dict[str, Any]:
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        return {
            "has_time_concept": False,
            "time_center": None,
            "confidence_radius": None,
            "is_history_mode": False,
            "confidence": 1.0,
        }

    endpoint = f"{base_url}/chat/completions"
    model = config.get("model", "")
    api_key = config.get("api_key", "")
    timeout = config.get("timeout", 120)

    now_ts = int(time.time())
    prompt = (
        "分析以下用户输入中是否包含时间概念。\n"
        "时间词举例：\n"
        "  历史类：前/以前/曾经/当年/那时/去年/上次/之前/过去\n"
        "  当前类：现在/目前/最近/当前/今天/这次\n\n"
        "请以 JSON 格式输出：\n"
        "{\n"
        '  "has_time_concept": true/false,\n'
        '  "is_history_mode": true/false,\n'
        '  "center_days_ago": <距今天数，无时间概念时为0>,\n'
        '  "radius_days": <置信半径天数，无时间概念时为0>,\n'
        '  "confidence": <0.0-1.0>\n'
        "}\n\n"
        "注意：绝对不允许使用 ```json 等任何 Markdown 标记将其包裹，直接输出大括号开头的内容。\n"
        f"当前时间戳：{now_ts}\n"
        f"用户输入：{query}"
    )

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只输出纯JSON格式，不说其他话。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
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
            content = _extract_json_str(content)
            parsed = json.loads(content)

            has_time = parsed.get("has_time_concept", False)
            center_days_ago = parsed.get("center_days_ago", 0)
            radius_days = parsed.get("radius_days", 0)
            confidence = parsed.get("confidence", 1.0)
            is_history = parsed.get("is_history_mode", False)

            time_center = now_ts - int(center_days_ago * 86400) if has_time else None
            conf_radius = float(radius_days) if has_time else None

            return {
                "has_time_concept": has_time,
                "time_center": time_center,
                "confidence_radius": conf_radius,
                "is_history_mode": is_history,
                "confidence": confidence,
            }
    except Exception as e:
        logger.error(f"时间分析 LLM 请求失败: {e}")
        return {
            "has_time_concept": False,
            "time_center": None,
            "confidence_radius": None,
            "is_history_mode": False,
            "confidence": 1.0,
        }

def _preprocess(query: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """阶段一：时间概念分析"""
    return _call_llm_time_analysis(query, config)

def _should_suppress(session_id: str) -> bool:
    """检查是否处于静默期，抑制注入"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*), SUM(LENGTH(raw_text)) FROM memory_fragments WHERE session_id=?",
            (session_id,)
        )
        row = cursor.fetchone()
        count = row[0] if row and row[0] else 0
        chars = row[1] if row and row[1] else 0
        return count < INJECT_SUPPRESS_MSG_COUNT and chars < INJECT_SUPPRESS_CHARS
    except Exception as e:
        logger.error(f"抑制检查失败: {e}")
        return False
    finally:
        conn.close()

def _build_candidates(query: str, query_keywords: List[str], session_id: str, is_history_mode: bool) -> Set[str]:
    """阶段二：双路召回（图谱 + FTS5）"""
    candidate_ids = set()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # 路径 A — 图谱检索
        status_clause = "status IN ('active', 'superseded')" if is_history_mode else "status = 'active'"
        
        for entity in query_keywords:
            cursor.execute(
                f"""
                SELECT node_id, fragment_id FROM graph_nodes
                WHERE (entity_name LIKE ? OR ? LIKE '%' || entity_name || '%')
                  AND session_id = ? AND {status_clause}
                """,
                (f"%{entity}%", entity, session_id),
            )
            rows = cursor.fetchall()
            for row in rows:
                candidate_ids.add(row["fragment_id"])
                cursor.execute(
                    """
                    SELECT gn.fragment_id FROM graph_edges ge
                    JOIN graph_nodes gn ON ge.to_node_id = gn.node_id
                    WHERE ge.from_node_id = ? AND ge.status = 'active'
                    ORDER BY ge.affinity DESC
                    """,
                    (row["node_id"],),
                )
                for edge_row in cursor.fetchall():
                    candidate_ids.add(edge_row["fragment_id"])
                    
        # 公共库图谱节点
        for entity in query_keywords:
            cursor.execute(
                f"""
                SELECT gn.fragment_id FROM graph_nodes gn
                JOIN public_library pl ON gn.fragment_id = pl.source_fragment_id
                WHERE (gn.entity_name LIKE ? OR ? LIKE '%' || gn.entity_name || '%')
                  AND gn.{status_clause}
                """,
                (f"%{entity}%", entity),
            )
            for row in cursor.fetchall():
                candidate_ids.add(row["fragment_id"])
                
        # 路径 B — FTS5 检索
        if query_keywords:
            match_expr = " OR ".join(query_keywords)
            cursor.execute(
                """
                SELECT fragments_fts.fragment_id FROM fragments_fts
                JOIN memory_fragments mf ON fragments_fts.fragment_id = mf.fragment_id
                LEFT JOIN public_library pl ON fragments_fts.fragment_id = pl.source_fragment_id
                WHERE fragments_fts MATCH ?
                  AND (mf.session_id = ? OR pl.public_id IS NOT NULL)
                LIMIT 20
                """,
                (match_expr, session_id),
            )
            for row in cursor.fetchall():
                candidate_ids.add(row["fragment_id"])
    except Exception as e:
        logger.error(f"构建候选集失败: {e}")
    finally:
        conn.close()
        
    return candidate_ids

def _fetch_lightweight_fields(candidate_ids: Set[str], is_history_mode: bool) -> List[Dict[str, Any]]:
    if not candidate_ids:
        return []
    conn = get_connection()
    result = []
    try:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in candidate_ids)
        cursor.execute(
            f"""
            SELECT mf.fragment_id, mf.session_id, mf.created_at, mf.keywords, mf.summary, mf.weight,
                   json_group_object(ft.tag_type, ft.tag_value) as tags_json
            FROM memory_fragments mf
            LEFT JOIN fragment_tags ft ON mf.fragment_id = ft.fragment_id
            WHERE mf.fragment_id IN ({placeholders})
            GROUP BY mf.fragment_id
            """,
            tuple(candidate_ids)
        )
        for row in cursor.fetchall():
            frag = dict(row)
            tags_json = frag.pop('tags_json', '{}')
            try:
                parsed_tags = json.loads(tags_json) if tags_json else {}
                # Remove null keys resulting from LEFT JOIN when there are no tags
                parsed_tags = {k: v for k, v in parsed_tags.items() if k is not None}
                frag['tags'] = parsed_tags
            except:
                frag['tags'] = {}
                
            # 检查是否为 superseded
            cursor.execute(
                "SELECT 1 FROM graph_nodes WHERE fragment_id = ? AND status = 'superseded'",
                (frag["fragment_id"],)
            )
            is_sup = cursor.fetchone() is not None
            if is_sup and not is_history_mode:
                continue
            result.append(frag)
    except Exception as e:
        logger.error(f"获取轻量字段失败: {e}")
    finally:
        conn.close()
    return result

def _assess_confidence(
    fragments: List[Dict[str, Any]], 
    query: str, 
    query_keywords: List[str],
    session_id: str, 
    time_info: Dict[str, Any],
    max_weight: float
) -> List[Dict[str, Any]]:
    """阶段三/四/五之间的置信度判断"""
    is_cross_triggered = False
    
    top1_score = fragments[0].get("score", 0) if fragments else 0
    if len(fragments) < 2 or top1_score < THRESHOLD_LOW:
        # Trigger cross session
        is_cross_triggered = True
        
    if "@session-" in query or "上次我们聊的" in query or "之前讨论过" in query:
        is_cross_triggered = True
        
    if is_cross_triggered:
        from memoir.cross_session import search
        cross_fragments = search(
            query=query,
            query_keywords=query_keywords,
            from_session_id=session_id,
            time_info=time_info,
            max_weight=max_weight
        )
        fragments.extend(cross_fragments)
        fragments.sort(key=lambda x: x.get('score', 0), reverse=True)
        
    return fragments[:20]

def _call_llm_for_chain_summarize(query: str, chain_texts: List[str], config: Dict[str, Any]) -> str:
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        return "\n".join(chain_texts)
        
    endpoint = f"{base_url}/chat/completions"
    model = config.get("model", "")
    api_key = config.get("api_key", "")
    timeout = config.get("timeout", 120)
    
    chain_str = "\n".join([f"- {t}" for t in chain_texts])
    prompt = (
        "以下是一些按照时间变迁产生的事实链条记录：\n"
        f"{chain_str}\n\n"
        f"用户当前的检索词是：{query}\n\n"
        "请帮我提炼出这些事实记录中，与用户检索词相关的事实变迁路线，合并成一段简练连贯的摘要。\n"
        "要求：\n"
        "1. 剔除无关的生活细节或游记等噪声。\n"
        "2. 突出核心事实的转变过程（如职业、地点等）。\n"
        "3. 直接输出摘要文本，不要包含多余寒暄。"
    )
    
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个精准的事实变迁提炼器，负责过滤噪声并总结客观事实。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
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
            return content.strip()
    except Exception as e:
        logger.error(f"长链路提炼 LLM 请求失败: {e}")
        return "\n".join(chain_texts)

def _load_fragments(fragments: List[Dict[str, Any]], query: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """阶段四：决定加载深度（按排名加载 raw_text 或 summary），并处理图谱强制加载"""
    conn = get_connection()
    additional_fragments = {} # fragment_id -> fragment
    global_visited_fids = {f["fragment_id"] for f in fragments}
    
    try:
        cursor = conn.cursor()
        non_core_count = 0
        for frag in fragments:
            fid = frag["fragment_id"]
            need_raw = False
            non_core_count += 1
            if non_core_count <= TOP_FULL_LOAD:
                need_raw = True
                    
            if need_raw:
                cursor.execute(
                    "SELECT raw_text, gc_deleted_at FROM memory_fragments WHERE fragment_id = ?",
                    (fid,)
                )
                row = cursor.fetchone()
                if row:
                    frag["_loaded_raw_text"] = row["raw_text"] if row["raw_text"] else ""
                    frag["gc_deleted_at"] = row["gc_deleted_at"]
            else:
                frag["_loaded_raw_text"] = ""
                
            cursor.execute(
                """
                SELECT node_id, status, superseded_at, superseded_reason, triggered_by_fragment_ids 
                FROM graph_nodes 
                WHERE fragment_id = ? AND status = 'superseded'
                """,
                (fid,)
            )
            row = cursor.fetchone()
            if row:
                frag["_is_superseded"] = True
                frag["superseded_at"] = row["superseded_at"]
                frag["superseded_reason"] = row["superseded_reason"]
                
                # 开始 BFS 遍历
                start_node_id = row["node_id"]
                queue = deque([(start_node_id, 0)])
                visited_nodes = {start_node_id}
                
                chain_frags = []
                
                while queue:
                    current_node_id, depth = queue.popleft()
                    
                    # 首先处理当前 node 本身是否需要加载触发它的片段
                    cursor.execute(
                        "SELECT triggered_by_fragment_ids FROM graph_nodes WHERE node_id = ?",
                        (current_node_id,)
                    )
                    curr_node = cursor.fetchone()
                    if curr_node and curr_node["triggered_by_fragment_ids"]:
                        try:
                            t_ids = json.loads(curr_node["triggered_by_fragment_ids"])
                            for t_id in t_ids:
                                if t_id not in additional_fragments and t_id != fid and t_id not in global_visited_fids:
                                    # 强制回表取
                                    cursor.execute(
                                        "SELECT * FROM memory_fragments WHERE fragment_id = ?",
                                        (t_id,)
                                    )
                                    tf_row = cursor.fetchone()
                                    if tf_row:
                                        t_frag = dict(tf_row)
                                        t_frag["_loaded_raw_text"] = t_frag["raw_text"] if t_frag["raw_text"] else ""
                                        t_frag["gc_deleted_at"] = t_frag.get("gc_deleted_at")
                                        t_frag["tags"] = {"core": "forced"} # 强制标记以确保注入
                                        t_frag["_is_associated"] = True
                                        global_visited_fids.add(t_id)
                                        chain_frags.append(t_frag)
                        except Exception as e:
                            logger.error(f"解析 triggered_by_fragment_ids 失败: {e}")
                    
                    if depth < GRAPH_TRAVERSE_MAX_DEPTH:
                        # 查找有效边
                        cursor.execute(
                            """
                            SELECT to_node_id FROM graph_edges 
                            WHERE from_node_id = ? 
                            AND relation_type IN ('supersedes', 'implies_outdated')
                            AND affinity >= ?
                            AND status = 'active'
                            
                            UNION
                            
                            SELECT from_node_id FROM graph_edges 
                            WHERE to_node_id = ? 
                            AND relation_type IN ('supersedes', 'implies_outdated')
                            AND affinity >= ?
                            AND status = 'active'
                            """,
                            (current_node_id, GRAPH_TRAVERSE_MIN_AFFINITY, current_node_id, GRAPH_TRAVERSE_MIN_AFFINITY)
                        )
                        neighbors = cursor.fetchall()
                        for nb in neighbors:
                            nb_id = nb[0]
                            if nb_id not in visited_nodes:
                                visited_nodes.add(nb_id)
                                queue.append((nb_id, depth + 1))
                                
                                # 将相邻节点的 fragment 加入强制加载
                                cursor.execute(
                                    "SELECT fragment_id FROM graph_nodes WHERE node_id = ?",
                                    (nb_id,)
                                )
                                nb_frag = cursor.fetchone()
                                if nb_frag:
                                    n_fid = nb_frag["fragment_id"]
                                    if n_fid not in additional_fragments and n_fid != fid and n_fid not in global_visited_fids:
                                        cursor.execute(
                                            "SELECT * FROM memory_fragments WHERE fragment_id = ?",
                                            (n_fid,)
                                        )
                                        nf_row = cursor.fetchone()
                                        if nf_row:
                                            n_frag = dict(nf_row)
                                            n_frag["_loaded_raw_text"] = n_frag["raw_text"] if n_frag["raw_text"] else ""
                                            n_frag["gc_deleted_at"] = n_frag.get("gc_deleted_at")
                                            n_frag["tags"] = {"core": "forced"}
                                            n_frag["_is_associated"] = True
                                            global_visited_fids.add(n_fid)
                                            chain_frags.append(n_frag)
                                            
                if len(chain_frags) > 3:
                    raw_texts = [f["_loaded_raw_text"] for f in chain_frags if f["_loaded_raw_text"]]
                    summary = _call_llm_for_chain_summarize(query, raw_texts, config)
                    pseudo_fid = f"chain_summary_{fid}"
                    pseudo_frag = {
                        "fragment_id": pseudo_fid,
                        "session_name": "系统链式提炼",
                        "created_at": int(time.time()),
                        "keywords": json.dumps(["事实变迁总结"]),
                        "weight": 1.0,
                        "_loaded_raw_text": summary,
                        "_is_associated": True
                    }
                    additional_fragments[pseudo_fid] = pseudo_frag
                else:
                    for cf in chain_frags:
                        additional_fragments[cf["fragment_id"]] = cf
                                            
        # 将 additional 插入结果
        # additional 会和原有 fragments 去重
        existing_fids = {f["fragment_id"] for f in fragments}
        for add_f in additional_fragments.values():
            if add_f["fragment_id"] not in existing_fids:
                fragments.insert(0, add_f) # 插到前面保证权重
                existing_fids.add(add_f["fragment_id"])
                
    except Exception as e:
        logger.error(f"加载片段深度失败: {e}")
    finally:
        conn.close()
        
    return fragments

def _format_output(fragments: List[Dict[str, Any]]) -> str:
    """七：注入格式化"""
    if not fragments:
        return ""

    lines = []
    total_chars = 0
    max_chars = MAX_INJECT_CHARS

    for frag in fragments[:MAX_RETURN]:
        summary = frag.get("summary", "")
        keywords_raw = frag.get("keywords", "[]")
        try:
            kw_list = json.loads(keywords_raw) if isinstance(keywords_raw, str) else keywords_raw
        except (json.JSONDecodeError, TypeError):
            kw_list = []
        tag_str = ", ".join(kw_list) if kw_list else "无标签"
        
        weight = frag.get("weight", 1.0)
        created_at = frag.get("created_at", 0)
        is_cross = frag.get("is_cross_session", False)
        date_str = time.strftime("%Y-%m-%d", time.localtime(created_at)) if created_at else "未知"
        
        session_name = frag.get("session_name", "当前会话")
        if is_cross:
            session_name += "（跨会话）"
            
        gc_deleted_at = frag.get("gc_deleted_at")
        if gc_deleted_at:
            gc_dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(gc_deleted_at))
            lines.append(f"> 💡 来源: {session_name} | 时间: {date_str} | 标签: {tag_str} | 权重: {weight}")
            lines.append(f"> ⚠️ 此记忆片段（{summary}）已于 {gc_dt} 删除。")
            lines.append("> 💡 提示：收藏或手动打分可延长存储时间")
            lines.append("")
            continue
            
        raw_text = frag.get("_loaded_raw_text", "")
        score = frag.get("score", 0)
        content = ""
        label = ""
        
        if score >= THRESHOLD_HIGH:
            content = raw_text if raw_text else summary
        else:
            content = summary
            label = " 【摘要，可请求全文】"

        entry = f"{content}{label}\n> 💡 来源: {session_name} | 时间: {date_str} | 标签: {tag_str} | 权重: {weight}"
        
        if frag.get("_is_superseded"):
            sup_at = frag.get("superseded_at", 0)
            sup_at_str = time.strftime("%Y-%m-%d", time.localtime(sup_at)) if sup_at else "未知"
            sup_reason = frag.get("superseded_reason", "已被新信息替代")
            entry += f"\n> ⚠️ 此信息已于 {sup_at_str} 过时：{sup_reason}"
            
        entry_len = len(entry)
        if total_chars + entry_len > max_chars:
            lines.append("【注意：已触发注入字符限额，更多记忆未展示】")
            break
            
        lines.append(entry)
        lines.append("")
        total_chars += entry_len

    return "\n".join(lines).strip()

def full_scan_search(query_terms: List[str], session_id: str) -> str:
    """全库FTS5扫描，绕开抑制和图谱，直接匹配原词"""
    if not query_terms:
        return ""
        
    conn = get_connection()
    fragments = []
    try:
        cursor = conn.cursor()
        match_expr = " OR ".join(query_terms)
        cursor.execute(
            """
            SELECT mf.fragment_id, mf.session_id, mf.created_at, mf.keywords, mf.summary, mf.raw_text, mf.weight
            FROM fragments_fts fts
            JOIN memory_fragments mf ON fts.fragment_id = mf.fragment_id
            LEFT JOIN public_library pl ON fts.fragment_id = pl.source_fragment_id
            WHERE fts.fragments_fts MATCH ?
              AND (mf.session_id = ? OR pl.public_id IS NOT NULL)
            LIMIT ?
            """,
            (match_expr, session_id, FULL_SCAN_MAX_RETURN)
        )
        for row in cursor.fetchall():
            frag = dict(row)
            frag["score"] = 1.0 # 强制给高分，确保全文输出
            frag["_loaded_raw_text"] = frag["raw_text"]
            fragments.append(frag)
    except Exception as e:
        logger.error(f"FTS全库扫描失败: {e}")
    finally:
        conn.close()
        
    # 重用 _format_output，临时放大限额
    global MAX_INJECT_CHARS
    old_max = MAX_INJECT_CHARS
    MAX_INJECT_CHARS = FULL_SCAN_MAX_CHARS
    out = _format_output(fragments)
    MAX_INJECT_CHARS = old_max
    return out

def retrieve(query: str, session_id: str, config: Dict[str, Any]) -> str:
    """主入口，执行五阶段流程"""
    # 阶段一：预处理，增加抑制检查
    if _should_suppress(session_id):
        return ""
        
    time_info = _preprocess(query, config)
    if time_info.get("confidence", 1.0) < 0.6:
        return "⏳ 您提到的时间有些模糊，请确认一下您指的是大约什么时候？"
        
    query_keywords = _tokenize_query(query)
    
    # 阶段二：双路召回（图谱 + FTS5）
    candidate_ids = _build_candidates(query, query_keywords, session_id, time_info.get("is_history_mode", False))
    
    if not candidate_ids:
        cross_fragments = _assess_confidence([], query, query_keywords, session_id, time_info, 1.0)
        return _format_output(cross_fragments)
        
    # 获取轻量字段
    fragments = _fetch_lightweight_fields(candidate_ids, time_info.get("is_history_mode", False))
    
    # 阶段三：统一评分 (调用 memoir.scorer)
    max_weight = max((f.get("weight", 1.0) for f in fragments), default=1.0) if fragments else 1.0
    
    t_center_ts = time_info.get("time_center")
    if t_center_ts is None:
        t_center_ts = float(int(time.time()))
    else:
        t_center_ts = float(t_center_ts)
        
    t_radius_days = time_info.get("confidence_radius")
    if t_radius_days is None:
        t_radius_days = float("inf")
    else:
        t_radius_days = float(t_radius_days)

    fragments = score_fragments(
        fragments=fragments,
        t_center_ts=t_center_ts,
        t_radius_days=t_radius_days,
        has_time_concept=time_info.get("has_time_concept", False),
        max_weight=max_weight,
        query_terms=query_keywords
    )
    
    # 阶段三/四/五之间的置信度判断
    fragments = _assess_confidence(fragments, query, query_keywords, session_id, time_info, max_weight)
    
    # 阶段四：决定加载深度
    fragments = _load_fragments(fragments, query, config)
    
    # 七：注入格式化
    return _format_output(fragments)
