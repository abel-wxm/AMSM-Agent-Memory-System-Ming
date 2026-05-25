"""归档引擎 — sync_turn 钩子调用。

负责：
  - 按三模式加权切分对话为语义片段（暂以500字限长进行基础切分）
  - 调用辅助模型生成 summary 和 keywords
  - 写入 memory_fragments（含所有必要字段）
  - 同步写入 fragments_fts 全文索引
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from memoir.db.conn import get_connection

logger = logging.getLogger(__name__)


def _call_llm_for_archive(text: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """调用辅助大模型提取摘要和关键词。

    预期返回格式:
    {
        "summary": "不超过50字的摘要",
        "keywords": ["关键词1", "关键词2", "关键词3"]
    }
    """
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        return {"summary": text[:40], "keywords": ["无配置"]}

    endpoint = f"{base_url}/chat/completions"
    model = config.get("model", "")
    api_key = config.get("api_key", "")
    timeout = config.get("timeout", 120)

    prompt = (
        "请为以下对话文本提取一段不超过50字的精炼摘要，\n"
        "并提取最多 8 个核心关键词。注意：提取关键词时不要死板地只从原文中截取，\n"
        "请根据语义适当补充关联词或上位概念（例如原文说“哈士奇”，关键词里可以加上“狗”），以提高后续的检索命中率。\n\n"
        "同时判断该段内容是否包含用户的身份信息、职业、重大事实（如姓名、职业、家庭成员、重大生活事件等），\n"
        "如果是，在JSON中加入 \"is_identity_fact\": true，否则不加此字段。\n\n"
        "请务必且只能以JSON格式输出，输出格式必须严格符合：{\"summary\":\"...\", \"keywords\":[...], \"is_identity_fact\": true/false}\n"
        "对话文本：\n" + text
    )

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个严格输出JSON格式的归档助手。"},
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
            try:
                # 尝试解析模型返回的 JSON
                parsed = json.loads(content)
                return {
                    "summary": parsed.get("summary", text[:40]),
                    "keywords": parsed.get("keywords", ["提取失败"]),
                    "is_identity_fact": parsed.get("is_identity_fact", False)
                }
            except json.JSONDecodeError:
                return {"summary": content[:40], "keywords": ["格式错误"]}
    except Exception as e:
        logger.error(f"LLM API 归档请求失败: {e}")
        return {"summary": text[:40], "keywords": ["API报错"]}


def archive_fragment(
    session_id: str,
    user_content: str,
    assistant_content: str,
    config: Dict[str, Any],
    *,
    channel: str = "cli",
    tags: Optional[Dict[str, str]] = None
) -> List[str]:
    """切分对话并归档为 memory_fragments。"""
    raw_text = f"User: {user_content}\nAssistant: {assistant_content}"
    
    # 长度限制权重基础切分逻辑：超过500字则截断（后续可增加语义/时间切分）
    fragments = []
    chunk_size = 500
    for i in range(0, len(raw_text), chunk_size):
        fragments.append(raw_text[i:i+chunk_size])

    fragment_ids = []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        now = int(time.time())
        
        # 计算当前会话已有消息数和字数，判断是否处于“静默期”
        cursor.execute(
            "SELECT COUNT(*), SUM(LENGTH(raw_text)) FROM memory_fragments WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        msg_count = row[0] if row[0] else 0
        total_chars = row[1] if row[1] else 0
        
        # 根据 Record 1：< 20 条 且 < 500 字，不触发提炼和检索
        is_silent = (msg_count < 20 and total_chars < 500)
        
        for text_chunk in fragments:
            if is_silent:
                summary = ""
                keywords_json = "[]"
                is_identity_fact = False
            else:
                llm_res = _call_llm_for_archive(text_chunk, config)
                summary = llm_res.get("summary", "")
                keywords_json = json.dumps(llm_res.get("keywords", []), ensure_ascii=False)
                is_identity_fact = llm_res.get("is_identity_fact", False)
            
            fragment_id = str(uuid.uuid4())
            weight = 2.5 if is_identity_fact else 1.0
            
            # 1. 写入 memory_fragments 主表 (移除稀疏字段)
            cursor.execute(
                """
                INSERT INTO memory_fragments (
                    fragment_id, session_id, created_at, summary, keywords, 
                    raw_text, weight, source_msg_ids, last_accessed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fragment_id, session_id, now, summary, keywords_json, 
                 text_chunk, weight, "[]", now)
            )
            mf_rowid = cursor.lastrowid
            
            # 2. 写入 fragment_tags
            if is_identity_fact:
                cursor.execute(
                    "INSERT INTO fragment_tags (fragment_id, tag_type, tag_value, created_at) VALUES (?, ?, ?, ?)",
                    (fragment_id, "identity_fact", "1", now)
                )
                
            current_tags = dict(tags) if tags else {}
            current_tags["channel"] = channel
            
            # 判断是否需要归档到 public_library
            is_public = current_tags.pop("is_public", None)
            public_id = None
            if is_public == "1":
                public_id = str(uuid.uuid4())
                added_by = current_tags.get("archive_type", "ai_auto")
                if "favorite" in current_tags:
                    added_by = "user_favorite"
                
                cursor.execute(
                    """
                    INSERT INTO public_library (
                        public_id, source_fragment_id, raw_text, summary, keywords, added_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (public_id, fragment_id, text_chunk, summary, keywords_json, added_by, now)
                )
                current_tags["public"] = public_id

            # 遍历并写入所有有值的 tag
            for tag_type, tag_value in current_tags.items():
                if tag_type == "archive_type" and tag_value == "ai_auto":
                    continue  # ai_auto 默认不写入
                cursor.execute(
                    """
                    INSERT INTO fragment_tags (
                        fragment_id, tag_type, tag_value, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (fragment_id, tag_type, str(tag_value), now)
                )
            
            # 3. 同步写入 fragments_fts
            cursor.execute(
                """
                INSERT INTO fragments_fts (
                    rowid, fragment_id, summary, keywords
                ) VALUES (?, ?, ?, ?)
                """,
                (mf_rowid, fragment_id, summary, keywords_json)
            )
            
            if not is_silent:
                fragment_ids.append(fragment_id)
            
        conn.commit()
    except Exception as e:
        logger.error(f"归档落库失败: {e}")
        with open("/home/abel/amsm_debug.log", "a") as f:
            f.write(f"Archive exception: {e}\n")
        conn.rollback()
    finally:
        conn.close()

    return fragment_ids
