import unittest
import os
import time
import uuid
from unittest.mock import patch

from memoir.db.conn import get_connection
from memoir.archive import archive_fragment
from memoir.graph import update_graph
from memoir.retrieval import retrieve
from memoir.gc import run_gc

class TestE2E(unittest.TestCase):
    def setUp(self):
        self.db_path = f"/tmp/amsm_test_e2e_{uuid.uuid4()}.db"
        os.environ["AMSM_DB_PATH"] = self.db_path
        self.conn = get_connection()
        self.cursor = self.conn.cursor()
        from memoir.db.schema import ALL_CREATE_STATEMENTS
        for stmt in ALL_CREATE_STATEMENTS:
            self.cursor.execute(stmt)
        self.conn.commit()

        self.config = {
            "base_url": "mock",
            "model": "mock",
            "api_key": "mock"
        }

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    @patch("memoir.archive._call_llm_for_archive")
    @patch("memoir.graph._call_llm_for_graph_extraction")
    @patch("memoir.graph._call_llm_for_conflict")
    @patch("memoir.graph._call_llm_for_session_summarize")
    def test_e2e_flow(self, mock_sum, mock_conflict, mock_extract, mock_archive):
        # 1. 创建会话
        session_id = str(uuid.uuid4())
        now = int(time.time())
        self.cursor.execute("INSERT INTO sessions (session_id, created_at, updated_at) VALUES (?, ?, ?)", (session_id, now, now))
        self.conn.commit()

        # 模拟LLM返回
        mock_archive.return_value = {
            "summary": "用户是结构工程师",
            "keywords": ["结构工程师"],
            "is_core": 1,
            "is_public": 0
        }
        mock_extract.return_value = {
            "entities": [{"name": "用户", "type": "person"}, {"name": "结构工程师", "type": "profession"}],
            "relations": [{"source": "用户", "target": "结构工程师", "type": "works_at", "affinity": 0.9}]
        }
        mock_conflict.return_value = {"conflicts": []}

        # 归档
        fids = archive_fragment(session_id, "我是一名结构工程师", "", self.config)
        fid1 = fids[0]
        
        # 图谱构建
        update_graph(fid1, session_id, "用户是结构工程师", ["结构工程师"], self.config)

        # 验证图谱节点
        self.cursor.execute("SELECT status FROM graph_nodes WHERE fragment_id = ?", (fid1,))
        nodes = self.cursor.fetchall()
        self.assertEqual(len(nodes), 2)
        for n in nodes:
            self.assertEqual(n["status"], "active")
            
        # 2. 检索
        with patch("memoir.retrieval._call_llm_time_analysis") as mock_time:
            mock_time.return_value = {
                "has_time_concept": False,
                "time_center": None,
                "confidence_radius": None,
                "is_history_mode": False,
                "confidence": 1.0,
            }
            ans = retrieve("我是结构工程师吗？", session_id, self.config)
            self.assertIn("结构工程师", ans)

        # 3. 模拟冲突（转行做电商）
        mock_archive.return_value = {
            "summary": "用户转行做电商",
            "keywords": ["电商"],
            "is_core": 1,
            "is_public": 0
        }
        mock_extract.return_value = {
            "entities": [{"name": "用户", "type": "person"}, {"name": "电商", "type": "profession"}],
            "relations": [{"source": "用户", "target": "电商", "type": "works_at", "affinity": 0.9}]
        }
        
        # 获取要被替换的旧节点
        self.cursor.execute("SELECT node_id FROM graph_nodes WHERE fragment_id = ? LIMIT 1", (fid1,))
        old_node_id = self.cursor.fetchone()["node_id"]
        mock_conflict.return_value = {"conflicts": [{"node_id": old_node_id, "reason": "用户转行了"}]}

        fids2 = archive_fragment(session_id, "我现在转行做电商了", "", self.config)
        fid2 = fids2[0]
        update_graph(fid2, session_id, "用户转行做电商", ["电商"], self.config)

        # 验证冲突更新
        self.cursor.execute("SELECT status FROM graph_nodes WHERE node_id = ?", (old_node_id,))
        self.assertEqual(self.cursor.fetchone()["status"], "superseded")

        # 4. 跨会话检索验证
        session2 = str(uuid.uuid4())
        self.cursor.execute("INSERT INTO sessions (session_id, created_at, updated_at) VALUES (?, ?, ?)", (session2, now, now))
        self.conn.commit()
        
        with patch("memoir.retrieval._call_llm_time_analysis") as mock_time:
            mock_time.return_value = {
                "has_time_concept": False,
                "time_center": None,
                "confidence_radius": None,
                "is_history_mode": False,
                "confidence": 1.0,
            }
            ans = retrieve("@全局 我转行做电商了吗？", session2, self.config)
            # fid2 包含电商，应该被找出来
            self.assertIn("电商", ans)

        # 5. GC冷存储测试
        res = run_gc()
        # 由于当前时间没超基础限制（且是 core），所以不应该被清理
        self.assertEqual(len(res["phase1_removed"]), 0)

        print("test_e2e_flow PASS")

if __name__ == "__main__":
    unittest.main()
