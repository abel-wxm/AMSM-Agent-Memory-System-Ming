"""AMSM 冒烟测试。"""

import unittest
import os
import sys
import time
import uuid

# 确保能 import memoir
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from memoir.db.conn import get_connection

class TestSmoke(unittest.TestCase):
    """基础冒烟测试，确认所有模块可导入及数据库可用。"""

    def setUp(self):
        self.conn = get_connection()
        self.cursor = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def test_imports(self):
        """测试各模块是否可导入。"""
        import memoir
        from memoir import config
        from memoir import archive
        from memoir import graph
        from memoir import retrieval
        from memoir import evolution
        from memoir import gc
        from memoir.db import schema
        from memoir.db import conn
        
        self.assertTrue(True)

    def test_database_flow(self):
        """测试数据库的读写、外键及 FTS5 功能。"""
        session_id = str(uuid.uuid4())
        fragment_id = str(uuid.uuid4())
        now = int(time.time())

        # 写入测试 session
        self.cursor.execute(
            """
            INSERT INTO sessions (
                session_id, created_at, updated_at, name, channel
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, now, now, "Test Session", "cli")
        )

        # 写入测试 fragment
        self.cursor.execute(
            """
            INSERT INTO memory_fragments (
                fragment_id, session_id, created_at, summary, keywords, 
                raw_text, last_accessed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (fragment_id, session_id, now, "测试摘要", '["测试", "TDD"]', 
             "这是一条冒烟测试记录", now)
        )

        # 同步写入 FTS
        self.cursor.execute(
            """
            INSERT INTO fragments_fts (
                rowid, fragment_id, summary, keywords
            ) VALUES (?, ?, ?, ?)
            """,
            (self.cursor.lastrowid, fragment_id, "测试摘要", '["测试", "TDD"]')
        )
        self.conn.commit()

        # 断言 1: 50ms内通过 session_id 查出
        start_time = time.time()
        self.cursor.execute(
            "SELECT * FROM memory_fragments WHERE session_id = ?",
            (session_id,)
        )
        row = self.cursor.fetchone()
        duration_ms = (time.time() - start_time) * 1000
        
        self.assertLess(duration_ms, 50.0, "查询时间超过50ms")
        self.assertIsNotNone(row)
        self.assertEqual(row["fragment_id"], fragment_id)
        self.assertEqual(row["raw_text"], "这是一条冒烟测试记录")
        self.assertIsInstance(row["created_at"], int)

        # 断言 2: FTS5 能通过关键词检索到该 fragment
        self.cursor.execute(
            """
            SELECT fragment_id FROM fragments_fts 
            WHERE fragments_fts MATCH '测试摘要'
            """
        )
        fts_row = self.cursor.fetchone()
        self.assertIsNotNone(fts_row, "FTS5 未检索到结果")
        self.assertEqual(fts_row[0], fragment_id)

        print("test_database_flow PASS")

if __name__ == "__main__":
    unittest.main()
