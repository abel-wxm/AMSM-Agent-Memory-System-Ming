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
        "请为以下对话文本提取一段不超过50字的精炼摘要，"
        "并提取3-5个核心关键词。\n\n"
        "请务必且只能以JSON格式输出，字段名为 `summary` 和 `keywords`。\n"
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
                    "keywords": parsed.get("keywords", ["提取失败"])
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
        
        for text_chunk in fragments:
            llm_res = _call_llm_for_archive(text_chunk, config)
            summary = llm_res.get("summary", "")
            keywords_json = json.dumps(llm_res.get("keywords", []), ensure_ascii=False)
            
            fragment_id = str(uuid.uuid4())
            
            # 1. 写入 memory_fragments
            cursor.execute(
                """
                INSERT INTO memory_fragments (
                    fragment_id, session_id, created_at, summary, keywords, 
                    raw_text, weight, is_manual, is_core, is_public, is_favorite,
                    channel, last_accessed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1.0, 0, 0, 0, 0, ?, ?)
                """,
                (fragment_id, session_id, now, summary, keywords_json, 
                 text_chunk, channel, now)
            )
            
            # 2. 同步写入 fragments_fts
            cursor.execute(
                """
                INSERT INTO fragments_fts (
                    rowid, fragment_id, summary, keywords
                ) VALUES (?, ?, ?, ?)
                """,
                (cursor.lastrowid, fragment_id, summary, keywords_json)
            )
            
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
