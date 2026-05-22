"""自进化模块 — Few-Shot 样本管理。

触发：用户修改 AI 生成的归档总结。
操作：存入 evolution_samples 表。
使用：下次归档时检索最近 5 条高权重样本作为 Few-Shot 注入。
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from memoir.db.conn import get_connection


def record_sample(
    ai_version: str,
    user_version: str,
    weight: float = 1.0,
) -> str:
    """记录 AI 原版 vs 用户修改版到 evolution_samples。

    Parameters
    ----------
    ai_version : str
        AI 生成的原始摘要。
    user_version : str
        用户修改后的版本。
    weight : float
        样本权重，默认 1.0。

    Returns
    -------
    str
        新样本的 sample_id。
    """
    sample_id = str(uuid.uuid4())
    now = int(time.time())

    # 计算简单的 diff_score：编辑距离比率
    max_len = max(len(ai_version), len(user_version), 1)
    common = sum(1 for a, b in zip(ai_version, user_version) if a == b)
    diff_score = 1.0 - (common / max_len)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO evolution_samples (
                sample_id, created_at, ai_version, user_version, diff_score, weight
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sample_id, now, ai_version, user_version, diff_score, weight),
        )
        conn.commit()
    finally:
        conn.close()

    return sample_id


def build_prompt(limit: int = 5) -> str:
    """检索最近高权重样本，构建 Few-Shot Prompt。

    Parameters
    ----------
    limit : int
        最多取多少条样本，默认 5。

    Returns
    -------
    str
        包含样本的 Prompt 文本，若无样本则返回空字符串。
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ai_version, user_version, weight
            FROM evolution_samples
            ORDER BY weight DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        return ""

    lines = [
        "以下是用户对 AI 摘要的修改样本，请参考用户的偏好风格来生成摘要：\n"
    ]
    for i, row in enumerate(rows, 1):
        lines.append(f"样本{i}：")
        lines.append(f"  AI原版：{row['ai_version']}")
        lines.append(f"  用户修改：{row['user_version']}")
        lines.append("")

    return "\n".join(lines).strip()


def mark_core(
    fragment_id: str,
    core_reason: str = "user_bookmarked",
) -> None:
    """将指定片段标记为核心记忆。

    Parameters
    ----------
    fragment_id : str
        要标记的片段 ID。
    core_reason : str
        标记原因：user_bookmarked / ai_classified / user_specified
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE memory_fragments
            SET is_core = 1, core_reason = ?
            WHERE fragment_id = ?
            """,
            (core_reason, fragment_id),
        )
        conn.commit()
    finally:
        conn.close()
