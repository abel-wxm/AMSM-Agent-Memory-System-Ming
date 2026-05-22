"""GC 冷存储模块。

两阶段执行：
  第一阶段（立即）：从 memory_fragments 删除 → fragments_fts 删除
      → 追加 archive/cold_memory.jsonl → graph_nodes.gc_pending=1
  第二阶段（180天后）：删除 gc_pending 节点及相关边

触发条件（三个同时满足）：
  - weight ≤ 3.0
  - 距 created_at 超过 基础时间(60天) × weight
  - 距 last_accessed_at 超过 基础时间(60天) × weight

禁止 GC 例外：is_core=1 / is_public=1 / node.status=superseded
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List

from memoir.db.conn import get_connection
from memoir.config_params import GC_BASE_DAYS, GC_PHASE2_DAYS, GC_MAX_WEIGHT
from memoir.cross_session import update_global_msd_timestamps

logger = logging.getLogger(__name__)


def run_gc() -> Dict[str, List[str]]:
    """执行 GC 冷存储扫描和清理。

    Returns
    -------
    dict
        包含:
        - ``phase1_removed`` (list[str]): 第一阶段删除的 fragment_id 列表
        - ``phase2_removed`` (list[str]): 第二阶段删除的 node_id 列表
        - ``skipped`` (list[str]): 因例外跳过的 fragment_id 列表
    """
    conn = get_connection()
    phase1_removed = []
    phase2_removed = []
    skipped = []
    
    try:
        cursor = conn.cursor()
        now = int(time.time())
        
        # Phase 1: Identify targets
        cursor.execute(
            """
            SELECT m.fragment_id, m.weight, m.created_at, m.last_accessed_at, m.is_core, m.is_public,
                   g.status, m.session_id, m.summary, m.keywords, m.raw_text, m.is_manual,
                   m.is_favorite, m.archive_type, m.channel, m.source_msg_ids, m.public_source_path, m.modified_log
            FROM memory_fragments m
            LEFT JOIN graph_nodes g ON m.fragment_id = g.fragment_id
            WHERE m.weight <= ?
            AND m.raw_text IS NOT NULL
            AND m.raw_text != ''
            """,
            (GC_MAX_WEIGHT,)
        )
        candidates = cursor.fetchall()
        
        cold_memory_path = "archive/cold_memory.jsonl"
        archive_dir = os.path.dirname(cold_memory_path)
        if archive_dir and not os.path.exists(archive_dir):
            os.makedirs(archive_dir, exist_ok=True)
            
        for row in candidates:
            fid = row["fragment_id"]
            weight = row["weight"]
            
            threshold = GC_BASE_DAYS * weight * 86400
            if (now - row["created_at"]) > threshold and (now - row["last_accessed_at"]) > threshold:
                if row["is_core"] == 1 or row["is_public"] == 1 or row["status"] == "superseded":
                    skipped.append(fid)
                    continue
                    
                # Execute Phase 1
                with open(cold_memory_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                    
                cursor.execute(
                    "UPDATE memory_fragments SET raw_text = '', gc_deleted_at = ? WHERE fragment_id = ?",
                    (now, fid)
                )
                cursor.execute("DELETE FROM fragments_fts WHERE fragment_id = ?", (fid,))
                cursor.execute(
                    "UPDATE graph_nodes SET gc_pending = 1, gc_pending_since = ? WHERE fragment_id = ?",
                    (now, fid)
                )
                phase1_removed.append(fid)

        # Phase 2: Identify targets
        cursor.execute(
            """
            SELECT node_id, fragment_id, session_id, gc_pending_since
            FROM graph_nodes
            WHERE gc_pending = 1
            """
        )
        p2_candidates = cursor.fetchall()
        affected_sessions = set()
        
        for row in p2_candidates:
            if (now - row["gc_pending_since"]) > GC_PHASE2_DAYS * 86400:
                nid = row["node_id"]
                fid = row["fragment_id"]
                sid = row["session_id"]
                
                cursor.execute("DELETE FROM graph_edges WHERE from_node_id = ? OR to_node_id = ?", (nid, nid))
                cursor.execute("DELETE FROM graph_nodes WHERE node_id = ?", (nid,))
                cursor.execute("DELETE FROM memory_fragments WHERE fragment_id = ?", (fid,))
                
                phase2_removed.append(nid)
                affected_sessions.add(sid)
                
        conn.commit()
        
        # Phase 2 follow-up
        for sid in affected_sessions:
            update_global_msd_timestamps(sid)
            
    except Exception as e:
        logger.error(f"GC运行失败: {e}")
        conn.rollback()
    finally:
        conn.close()
        
    return {
        "phase1_removed": phase1_removed,
        "phase2_removed": phase2_removed,
        "skipped": skipped
    }
