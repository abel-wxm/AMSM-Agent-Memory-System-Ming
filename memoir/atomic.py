"""原子化拆分引擎 — 处理 public_library 里的复杂长记录。"""

import json
import logging
import time
import urllib.request
import uuid
from typing import Any, Dict, List

from memoir.db.conn import get_connection

logger = logging.getLogger(__name__)


def _call_llm_for_atomic_split(text: str, config: Dict[str, Any]) -> List[str]:
    """调用大模型将整段记录拆分为原子事实数组。"""
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        return [text]

    endpoint = f"{base_url}/chat/completions"
    model = config.get("model", "")
    api_key = config.get("api_key", "")
    timeout = config.get("timeout", 120)

    prompt = (
        "以下是一段关于用户的历史记忆片段。\n"
        "请判断它是否包含多个独立的、散落的原子事实（例如：姓名、职业、住址、喜好等）。\n"
        "如果它是一个连贯的长篇故事，无法拆分，请返回一个空数组 `[]`。\n"
        "如果是可以拆分的散落事实，请将它们提炼为互相独立的、用词简练的原子句（每句必须主谓宾完整），并以 JSON 数组格式返回。\n"
        "输出格式必须为 JSON 数组，例如：[\"用户住在北京\", \"用户喜欢吃苹果\"]\n"
        "记忆片段：\n" + text
    )

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个严谨的信息拆分助手，严格输出 JSON 数组。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
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
                # 兼容部分模型依然把数组包在对象里的情况
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list):
                            return [str(item) for item in v]
                    return []
                elif isinstance(parsed, list):
                    return [str(item) for item in parsed]
                return []
            except json.JSONDecodeError:
                return []
    except Exception as e:
        logger.error(f"原子拆分 LLM 请求失败: {e}")
        return []


def run_atomic_extraction(config: Dict[str, Any]) -> int:
    """找出需要原子化的记录，提取并落库去重。返回新生成的原子事实数量。"""
    conn = get_connection()
    extracted_count = 0
    try:
        cursor = conn.cursor()
        
        # 1. 查出 is_atomic=0 且尚未处理（此处简化为所有 is_atomic=0 都尝试，实际可加状态字段）
        cursor.execute("SELECT public_id, raw_text, source_fragment_id, added_by FROM public_library WHERE is_atomic = 0")
        rows = cursor.fetchall()
        
        now = int(time.time())
        
        for row in rows:
            public_id = row["public_id"]
            raw_text = row["raw_text"]
            source_fragment_id = row["source_fragment_id"]
            added_by = row["added_by"]
            
            atom_facts = _call_llm_for_atomic_split(raw_text, config)
            if not atom_facts:
                continue
                
            for fact in atom_facts:
                # 去重检查：使用 FTS 或 LIKE。此处为了不侵入现有 FTS 表结构，用简单的 LIKE 检查。
                cursor.execute(
                    "SELECT public_id FROM public_library WHERE is_atomic = 1 AND raw_text LIKE ?",
                    (f"%{fact[:10]}%",)
                )
                similar_rows = cursor.fetchall()
                similar_ids = [r["public_id"] for r in similar_rows]
                
                new_public_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO public_library (
                        public_id, source_fragment_id, raw_text, summary, keywords, 
                        added_by, created_at, is_atomic, similar_to
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (new_public_id, source_fragment_id, fact, fact, "[\"原子事实\"]", added_by, now, json.dumps(similar_ids))
                )
                extracted_count += 1
                
                # 如果有相似项，互相更新 similar_to (由于是双向，简单起见，可以忽略更新老项的 similar_to，但建议同步)
                for sid in similar_ids:
                    cursor.execute("SELECT similar_to FROM public_library WHERE public_id = ?", (sid,))
                    old_sim_row = cursor.fetchone()
                    if old_sim_row:
                        try:
                            old_sim_list = json.loads(old_sim_row["similar_to"])
                        except:
                            old_sim_list = []
                        if new_public_id not in old_sim_list:
                            old_sim_list.append(new_public_id)
                            cursor.execute("UPDATE public_library SET similar_to = ? WHERE public_id = ?", (json.dumps(old_sim_list), sid))
                            
            # 更新原记录（标记为已提炼，或设 is_atomic=2 代表已处理）
            # 为了避免重复处理，将原记录标记为 is_atomic=2 
            cursor.execute("UPDATE public_library SET is_atomic = 2 WHERE public_id = ?", (public_id,))
            
        conn.commit()
    except Exception as e:
        logger.error(f"原子拆分过程失败: {e}")
        conn.rollback()
    finally:
        conn.close()
        
    return extracted_count
