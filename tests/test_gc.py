import unittest
import os
import time
import uuid
import json

from memoir.db.conn import get_connection
from memoir.gc import run_gc
from memoir.config_params import GC_BASE_DAYS, GC_PHASE2_DAYS

class TestGC(unittest.TestCase):
    def setUp(self):
        self.db_path = f"/tmp/amsm_test_gc_{uuid.uuid4()}.db"
        os.environ["AMSM_DB_PATH"] = self.db_path
        self.conn = get_connection()
        self.cursor = self.conn.cursor()
        from memoir.db.schema import ALL_CREATE_STATEMENTS
        for stmt in ALL_CREATE_STATEMENTS:
            self.cursor.execute(stmt)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_run_gc_phase1_and_phase2(self):
        session_id = str(uuid.uuid4())
        now = int(time.time())
        self.cursor.execute(
            "INSERT INTO sessions (session_id, created_at, updated_at) VALUES (?, ?, ?)",
            (session_id, now, now)
        )
        
        self.cursor.execute(
            "INSERT INTO global_graph_index (global_node_id, session_id, timestamps, created_at, updated_at) VALUES (?, ?, '[]', ?, ?)",
            (str(uuid.uuid4()), session_id, now, now)
        )

        fid1 = str(uuid.uuid4())
        past_time = now - (GC_BASE_DAYS + 1) * 86400
        
        self.cursor.execute(
            """
            INSERT INTO memory_fragments (
                fragment_id, session_id, created_at, summary, raw_text, weight, last_accessed_at, is_core, is_public
            ) VALUES (?, ?, ?, 'Test Frag 1', 'Raw text 1', 1.0, ?, 0, 0)
            """,
            (fid1, session_id, past_time, past_time)
        )
        self.cursor.execute(
            "INSERT INTO fragments_fts (rowid, fragment_id, summary) VALUES (1, ?, 'Test Frag 1')",
            (fid1,)
        )
        nid1 = str(uuid.uuid4())
        self.cursor.execute(
            """
            INSERT INTO graph_nodes (node_id, fragment_id, session_id, valid_from, status)
            VALUES (?, ?, ?, ?, 'active')
            """,
            (nid1, fid1, session_id, past_time)
        )
        self.conn.commit()

        res1 = run_gc()
        self.assertIn(fid1, res1["phase1_removed"])
        self.assertEqual(len(res1["phase2_removed"]), 0)

        self.cursor.execute("SELECT raw_text, gc_deleted_at FROM memory_fragments WHERE fragment_id = ?", (fid1,))
        row = self.cursor.fetchone()
        self.assertEqual(row["raw_text"], "")
        self.assertIsNotNone(row["gc_deleted_at"])
        
        self.cursor.execute("SELECT gc_pending FROM graph_nodes WHERE fragment_id = ?", (fid1,))
        g_row = self.cursor.fetchone()
        self.assertEqual(g_row["gc_pending"], 1)

        past_pending_time = now - (GC_PHASE2_DAYS + 1) * 86400
        self.cursor.execute(
            "UPDATE graph_nodes SET gc_pending_since = ? WHERE fragment_id = ?",
            (past_pending_time, fid1)
        )
        self.conn.commit()

        res2 = run_gc()
        self.assertIn(nid1, res2["phase2_removed"])

        self.cursor.execute("SELECT * FROM memory_fragments WHERE fragment_id = ?", (fid1,))
        self.assertIsNone(self.cursor.fetchone())
        self.cursor.execute("SELECT * FROM graph_nodes WHERE node_id = ?", (nid1,))
        self.assertIsNone(self.cursor.fetchone())
