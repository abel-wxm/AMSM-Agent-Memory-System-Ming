"""AMSM 图谱引擎 TDD 测试。"""

import unittest
import os
import sys
import time
import uuid
import json
from unittest.mock import patch, MagicMock

# 确保能 import memoir
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from memoir.db.conn import get_connection
from memoir.graph import update_graph

class TestGraph(unittest.TestCase):
    def setUp(self):
        self.db_path = f"/tmp/amsm_test_{uuid.uuid4()}.db"
        os.environ["AMSM_DB_PATH"] = self.db_path
        self.conn = get_connection()
        self.cursor = self.conn.cursor()
        from memoir.db.schema import ALL_CREATE_STATEMENTS
        for stmt in ALL_CREATE_STATEMENTS:
            self.cursor.execute(stmt)
        self.conn.commit()
        self.session_id = str(uuid.uuid4())
        self.now = int(time.time())
        
        # 初始化测试 session
        self.cursor.execute(
            """
            INSERT INTO sessions (
                session_id, created_at, updated_at, name, channel
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (self.session_id, self.now, self.now, "Graph Test Session", "cli")
        )
        
        # 预备两个 fragment_id (无需真的写入 memory_fragments，图谱只需ID引用，
        # 但考虑到 foreign key，我们需要插入假的 fragments)
        self.frag1_id = str(uuid.uuid4())
        self.frag2_id = str(uuid.uuid4())
        
        for fid in [self.frag1_id, self.frag2_id]:
            self.cursor.execute(
                """
                INSERT INTO memory_fragments (
                    fragment_id, session_id, created_at, summary, keywords, 
                    raw_text, weight, is_manual, is_core, is_public, is_favorite,
                    channel, last_accessed_at
                ) VALUES (?, ?, ?, '', '[]', '', 1.0, 0, 0, 0, 0, 'cli', ?)
                """,
                (fid, self.session_id, self.now, self.now)
            )
            
        self.conn.commit()
        
        self.config = {
            "base_url": "http://mock-api/v1",
            "model": "mock-model",
            "api_key": "mock-key",
            "timeout": 10
        }

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    @patch('memoir.graph._call_llm_for_graph_extraction')
    @patch('memoir.graph._call_llm_for_conflict')
    def test_graph(self, mock_conflict, mock_extract):
        # Step 1: 写入 fragment「用户是结构工程师」
        mock_extract.return_value = {
            "entities": [{"name": "用户", "type": "person"}, {"name": "结构工程师", "type": "profession"}],
            "relations": [{"source": "用户", "target": "结构工程师", "type": "works_at", "affinity": 0.9}]
        }
        mock_conflict.return_value = {"conflicts": []}
        
        node_ids_1 = update_graph(self.frag1_id, self.session_id, "用户是结构工程师", ["结构", "工程师"], self.config)
        self.assertEqual(len(node_ids_1), 2)
        
        # 检查 active 节点
        self.cursor.execute("SELECT status FROM graph_nodes WHERE node_id = ?", (node_ids_1[0],))
        self.assertEqual(self.cursor.fetchone()["status"], "active")
        
        # Step 2: 写入 fragment「用户转行做电商」
        mock_extract.return_value = {
            "entities": [{"name": "用户", "type": "person"}, {"name": "电商", "type": "profession"}],
            "relations": [{"source": "用户", "target": "电商", "type": "works_at", "affinity": 0.9}]
        }
        # 模拟冲突，替代刚才的第一个节点(即旧的"用户"或对应的职业节点)
        # 为简化，假定推翻了原本的 "结构工程师" 节点
        mock_conflict.return_value = {
            "conflicts": [{"node_id": node_ids_1[1], "reason": "转行导致前职业不再是当前主要职业"}]
        }
        
        node_ids_2 = update_graph(self.frag2_id, self.session_id, "用户转行做电商", ["转行", "电商"], self.config)
        
        # 断言: 旧节点 status=superseded，valid_until 不为 NULL
        self.cursor.execute("SELECT status, valid_until FROM graph_nodes WHERE node_id = ?", (node_ids_1[1],))
        old_node = self.cursor.fetchone()
        self.assertEqual(old_node["status"], "superseded")
        self.assertIsNotNone(old_node["valid_until"])
        
        # 断言: mutation_log 行数增加，reason 字段非空
        self.cursor.execute("SELECT count(*) as cnt FROM mutation_log")
        cnt = self.cursor.fetchone()["cnt"]
        self.assertGreaterEqual(cnt, 1)
        
        self.cursor.execute("SELECT reason FROM mutation_log WHERE node_id = ?", (node_ids_1[1],))
        log = self.cursor.fetchone()
        self.assertEqual(log["reason"], "转行导致前职业不再是当前主要职业")
        
        # 断言: 新节点 status=active
        self.cursor.execute("SELECT status FROM graph_nodes WHERE node_id = ?", (node_ids_2[1],))
        new_node = self.cursor.fetchone()
        self.assertEqual(new_node["status"], "active")

    def test_summarize_session_trigger(self):
        session_id = str(uuid.uuid4())
        session_created = int(time.time()) - 1000
        now = int(time.time())
        self.cursor.execute("INSERT INTO sessions (session_id, created_at, updated_at) VALUES (?, ?, ?)", (session_id, session_created, session_created))
        self.conn.commit()

        config = {}
        for i in range(20):
            fid = str(uuid.uuid4())
            self.cursor.execute(
                "INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, raw_text, last_accessed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (fid, session_id, now + i, f"Frag {i}", f"Raw {i}", now + i)
            )
            self.conn.commit()
            update_graph(fid, session_id, f"Frag {i}", [], config)

        self.cursor.execute("SELECT summary, keywords, summarize_count FROM global_graph_index WHERE session_id = ?", (session_id,))
        row = self.cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["summarize_count"], 1)
        self.assertIn("Frag 0", row["summary"])

if __name__ == "__main__":
    unittest.main()
