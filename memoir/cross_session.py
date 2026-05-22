"""
跨会话检索核心模块，包含快速通道、总表路由与全量遍历，以及总表和关联权重的维护。
"""
import json
import logging
import time
from typing import Any, Dict, List, Set, Tuple

from memoir.db.conn import get_connection
from memoir.retrieval import _build_candidates, _fetch_lightweight_fields
from memoir.scorer import score_msd_nodes, score_fragments

logger = logging.getLogger(__name__)

def update_global_msd_timestamps(session_id: str) -> None:
    """
    维护 global_graph_index 总表的 timestamps 字段。
    生成规则：
    - 以7天为一个窗口，窗口内相邻片段间隔 ≤1天(86400秒)视为连续
    - 连续满7天 → 取该窗口内所有片段 created_at 的中位值，生成一个时间戳
    - 中间断开（间隔>1天）→ 重新计时
    - 不足7天的尾段 → 不额外生成
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT fragment_id, created_at FROM memory_fragments WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,)
        )
        rows = cursor.fetchall()
        if not rows:
            return

        timestamps = []
        current_window = []

        for row in rows:
            frag_id = row["fragment_id"]
            created_at = row["created_at"]

            if not current_window:
                current_window.append((frag_id, created_at))
                continue

            last_created_at = current_window[-1][1]
            if created_at - last_created_at <= 86400:
                current_window.append((frag_id, created_at))
            else:
                # 间隔大于1天，判断当前窗口是否满7天
                window_duration = current_window[-1][1] - current_window[0][1]
                if window_duration >= 7 * 86400:
                    mid_idx = len(current_window) // 2
                    timestamps.append({
                        "ts": current_window[mid_idx][1],
                        "range_start_id": current_window[0][0],
                        "range_end_id": current_window[-1][0]
                    })
                current_window = [(frag_id, created_at)]

        # 检查最后一个窗口
        if current_window:
            window_duration = current_window[-1][1] - current_window[0][1]
            if window_duration >= 7 * 86400:
                mid_idx = len(current_window) // 2
                timestamps.append({
                    "ts": current_window[mid_idx][1],
                    "range_start_id": current_window[0][0],
                    "range_end_id": current_window[-1][0]
                })

        timestamps_json = json.dumps(timestamps)
        cursor.execute(
            "UPDATE global_graph_index SET timestamps = ? WHERE session_id = ?",
            (timestamps_json, session_id)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"update_global_msd_timestamps error: {e}")
    finally:
        conn.close()


def reset_affinity(from_session_id: str, to_session_id: str, new_affinity: int) -> None:
    """
    手动重置权重，大模型识别解除意图后指定值（-20到+20）。
    """
    new_affinity = max(-20, min(20, new_affinity))
    conn = get_connection()
    try:
        cursor = conn.cursor()
        now_ts = int(time.time())
        cursor.execute(
            """
            UPDATE session_graph_index 
            SET session_affinity = ?, last_hit_at = ?
            WHERE from_session_id = ? AND to_session_id = ?
            """,
            (new_affinity, now_ts, from_session_id, to_session_id)
        )
        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO session_graph_index (from_session_id, to_session_id, session_affinity, last_hit_at)
                VALUES (?, ?, ?, ?)
                """,
                (from_session_id, to_session_id, new_affinity, now_ts)
            )
        conn.commit()
    except Exception as e:
        logger.error(f"reset_affinity error: {e}")
    finally:
        conn.close()


def update_affinity(from_session_id: str, to_session_id: str, delta: int = 1) -> None:
    """
    命中后更新 session_graph_index 权重和 last_hit_at。
    - 正向/负向 delta
    - |session_affinity| > 10 时，delta 翻倍
    - 边界 [-20, 20]
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT session_affinity FROM session_graph_index WHERE from_session_id = ? AND to_session_id = ?",
            (from_session_id, to_session_id)
        )
        row = cursor.fetchone()
        current_affinity = row["session_affinity"] if row else 0

        actual_delta = delta
        if abs(current_affinity) > 10:
            actual_delta *= 2

        new_affinity = max(-20, min(20, current_affinity + actual_delta))
        now_ts = int(time.time())

        cursor.execute(
            """
            UPDATE session_graph_index 
            SET session_affinity = ?, last_hit_at = ?
            WHERE from_session_id = ? AND to_session_id = ?
            """,
            (new_affinity, now_ts, from_session_id, to_session_id)
        )
        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO session_graph_index (from_session_id, to_session_id, session_affinity, last_hit_at)
                VALUES (?, ?, ?, ?)
                """,
                (from_session_id, to_session_id, new_affinity, now_ts)
            )
        conn.commit()
    except Exception as e:
        logger.error(f"update_affinity error: {e}")
    finally:
        conn.close()


def _search_one_session(
    target_session_id: str,
    query: str,
    query_keywords: List[str],
    is_history_mode: bool,
    t_center_ts: float,
    t_radius_days: float,
    has_time_concept: bool,
    max_weight: float,
    session_factor: float
) -> List[Dict[str, Any]]:
    """
    对单会话的基础检索。
    """
    candidate_ids = _build_candidates(query, query_keywords, target_session_id, is_history_mode)
    if not candidate_ids:
        return []

    fragments = _fetch_lightweight_fields(candidate_ids, is_history_mode)

    for frag in fragments:
        frag["session_factor"] = session_factor
        frag["is_cross_session"] = True

    fragments = score_fragments(
        fragments=fragments,
        t_center_ts=t_center_ts,
        t_radius_days=t_radius_days,
        has_time_concept=has_time_concept,
        max_weight=max_weight,
        query_terms=query_keywords
    )

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sessions WHERE session_id = ?", (target_session_id,))
        row = cursor.fetchone()
        session_name = row["name"] if row else "未知会话"
        for frag in fragments:
            frag["session_name"] = session_name
    except Exception as e:
        logger.error(f"_search_one_session error: {e}")
    finally:
        conn.close()

    return fragments


def _rank_sessions(
    from_session_id: str,
    query_keywords: List[str]
) -> List[Tuple[str, float]]:
    """
    判定并执行快速通道、总表路由(global_graph_index) 或 遍历。
    返回 List[(session_id, session_factor)]
    """
    conn = get_connection()
    ranked_targets = []
    fast_lane_ids = set()

    try:
        cursor = conn.cursor()
        
        cursor.execute("SELECT fast_lane_sessions FROM sessions WHERE session_id = ?", (from_session_id,))
        row = cursor.fetchone()
        if row and row["fast_lane_sessions"]:
            try:
                fast_lane_list = json.loads(row["fast_lane_sessions"])
                for f_id in fast_lane_list:
                    fast_lane_ids.add(f_id)
            except Exception:
                pass

        cursor.execute("SELECT COUNT(*) as count FROM sessions")
        session_count_row = cursor.fetchone()
        session_count = session_count_row["count"] if session_count_row else 0

        cursor.execute(
            "SELECT to_session_id, session_affinity FROM session_graph_index WHERE from_session_id = ?",
            (from_session_id,)
        )
        affinity_map = {r["to_session_id"]: r["session_affinity"] for r in cursor.fetchall()}

        if session_count >= 5:
            # 总表路由
            cursor.execute(
                "SELECT session_id, summary, keywords FROM global_graph_index WHERE session_id != ?",
                (from_session_id,)
            )
            nodes = []
            for r in cursor.fetchall():
                node = dict(r)
                node["session_affinity"] = affinity_map.get(node["session_id"], 0)
                nodes.append(node)

            time_info_mock = {
                "has_time_concept": False # 默认无时间概念
            }
            scored_nodes = score_msd_nodes(nodes, time_info_mock, query_keywords)
            
            for node in scored_nodes:
                target_session_id = node["session_id"]
                affinity_bonus = node.get("affinity_bonus", 1.0)
                
                if affinity_bonus <= 0.0:
                    continue
                    
                if target_session_id in fast_lane_ids:
                    session_factor = min(2.0, affinity_bonus + 0.10)
                else:
                    session_factor = affinity_bonus
                    
                ranked_targets.append((target_session_id, session_factor))
                
        else:
            # 遍历路由
            cursor.execute(
                "SELECT session_id FROM sessions WHERE session_id != ? ORDER BY updated_at DESC",
                (from_session_id,)
            )
            for r in cursor.fetchall():
                target_session_id = r["session_id"]
                s_affinity = affinity_map.get(target_session_id, 0)
                affinity_bonus = max(0.0, min(2.0, 1.0 + s_affinity * 0.05))
                
                if affinity_bonus <= 0.0:
                    continue
                    
                if target_session_id in fast_lane_ids:
                    session_factor = min(2.0, affinity_bonus + 0.10)
                else:
                    session_factor = affinity_bonus
                    
                ranked_targets.append((target_session_id, session_factor))

    except Exception as e:
        logger.error(f"_rank_sessions error: {e}")
    finally:
        conn.close()

    return ranked_targets


def search(
    query: str,
    query_keywords: List[str],
    from_session_id: str,
    time_info: Dict[str, Any],
    max_weight: float
) -> List[Dict[str, Any]]:
    """
    跨会话检索主入口。
    供 retrieval.py 调用。
    """
    t_center_ts = time_info.get("time_center")
    if t_center_ts is None:
        t_center_ts = float(int(time.time()))
    t_radius_days = time_info.get("confidence_radius")
    if t_radius_days is None:
        t_radius_days = float("inf")
    has_time_concept = time_info.get("has_time_concept", False)
    is_history_mode = time_info.get("is_history_mode", False)

    ranked_targets = _rank_sessions(from_session_id, query_keywords)
    
    all_cross_fragments = []
    
    for target_session_id, session_factor in ranked_targets:
        frags = _search_one_session(
            target_session_id=target_session_id,
            query=query,
            query_keywords=query_keywords,
            is_history_mode=is_history_mode,
            t_center_ts=t_center_ts,
            t_radius_days=t_radius_days,
            has_time_concept=has_time_concept,
            max_weight=max_weight,
            session_factor=session_factor
        )
        if frags:
            all_cross_fragments.extend(frags)
            update_affinity(from_session_id, target_session_id, delta=1)

    all_cross_fragments.sort(key=lambda x: x.get("score", 0), reverse=True)
    return all_cross_fragments
