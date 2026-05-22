"""AMSM 核心记忆与自进化 TDD 测试。"""

import unittest
import os
import sys
import time
import uuid
import json
from unittest.mock import patch

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from memoir.db.conn import get_connection
from memoir.evolution import record_sample, build_prompt, mark_core
from memoir.retrieval import retrieve


class TestCoreMemory(unittest.TestCase):
    """核心记忆优先加载测试。"""

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
        self.config = {
            "base_url": "http://mock-api/v1",
            "model": "mock-model",
            "api_key": "mock-key",
            "timeout": 10,
        }

        # 创建会话
        self.cursor.execute(
            """
            INSERT INTO sessions (session_id, created_at, updated_at, name, channel)
            VALUES (?, ?, ?, ?, ?)
            """,
            (self.session_id, self.now, self.now, "Core Test", "cli"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    @patch("memoir.retrieval._call_llm_time_analysis")
    def test_core_priority(self, mock_time):
        """is_core=1 的片段应优先出现在 prefetch 返回内容中。"""
        mock_time.return_value = {
            "has_time_concept": False,
            "time_center": None,
            "confidence_radius": None,
            "is_history_mode": False,
            "confidence": 1.0,
        }

        # 1. 插入一条普通片段
        fid_normal = str(uuid.uuid4())
        self.cursor.execute(
            """
            INSERT INTO memory_fragments (
                fragment_id, session_id, created_at, summary, keywords,
                raw_text, weight, is_manual, is_core, is_public, is_favorite,
                channel, last_accessed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1.0, 0, 0, 0, 0, 'cli', ?)
            """,
            (fid_normal, self.session_id, self.now - 100,
             "普通片段摘要", '["普通"]', "普通片段的原文", self.now - 100),
        )
        self.cursor.execute(
            "INSERT INTO fragments_fts (rowid, fragment_id, summary, keywords) VALUES (?, ?, ?, ?)",
            (self.cursor.lastrowid, fid_normal, "普通片段摘要", '["普通"]'),
        )

        # 2. 插入一条 is_core=1 的片段
        fid_core = str(uuid.uuid4())
        self.cursor.execute(
            """
            INSERT INTO memory_fragments (
                fragment_id, session_id, created_at, summary, keywords,
                raw_text, weight, is_manual, is_core, core_reason, is_public, is_favorite,
                channel, last_accessed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1.0, 0, 1, 'user_bookmarked', 0, 0, 'cli', ?)
            """,
            (fid_core, self.session_id, self.now - 200,
             "核心记忆：用户的生日是5月1日", '["生日", "核心"]',
             "用户说他的生日是5月1日，这是核心信息", self.now - 200),
        )
        self.cursor.execute(
            "INSERT INTO fragments_fts (rowid, fragment_id, summary, keywords) VALUES (?, ?, ?, ?)",
            (self.cursor.lastrowid, fid_core, "核心记忆：用户的生日是5月1日", '["生日", "核心"]'),
        )
        self.conn.commit()

        # 执行 prefetch（查询随便什么）
        result = retrieve("你好", self.session_id, self.config)

        # 断言：返回内容中包含核心片段的内容（即使查询词不相关）
        self.assertIn("生日", result, "is_core=1 的片段应被强制加载")
        self.assertIn("5月1日", result)

        print("test_core_priority PASS")

    def test_mark_core(self):
        """测试 mark_core 功能。"""
        fid = str(uuid.uuid4())
        self.cursor.execute(
            """
            INSERT INTO memory_fragments (
                fragment_id, session_id, created_at, summary, keywords,
                raw_text, weight, is_manual, is_core, is_public, is_favorite,
                channel, last_accessed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1.0, 0, 0, 0, 0, 'cli', ?)
            """,
            (fid, self.session_id, self.now, "待标记", '["test"]', "原文", self.now),
        )
        self.conn.commit()

        # 标记为核心
        mark_core(fid, "user_bookmarked")

        # 验证
        self.cursor.execute(
            "SELECT is_core, core_reason FROM memory_fragments WHERE fragment_id = ?",
            (fid,),
        )
        row = self.cursor.fetchone()
        self.assertEqual(row["is_core"], 1)
        self.assertEqual(row["core_reason"], "user_bookmarked")

        print("test_mark_core PASS")


class TestEvolution(unittest.TestCase):
    """自进化 Few-Shot 样本测试。"""

    def setUp(self):
        self.db_path = f"/tmp/amsm_test_{uuid.uuid4()}.db"
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

    def test_record_and_build(self):
        """记录样本并构建 Few-Shot Prompt。"""
        # 记录一条样本
        sid = record_sample(
            ai_version="AI总结：用户喜欢吃面条",
            user_version="用户偏好面食，尤其是兰州拉面",
            weight=2.0,
        )
        self.assertTrue(len(sid) > 0)

        # 验证 DB 写入
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM evolution_samples WHERE sample_id = ?", (sid,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["ai_version"], "AI总结：用户喜欢吃面条")
        self.assertEqual(row["user_version"], "用户偏好面食，尤其是兰州拉面")
        self.assertEqual(row["weight"], 2.0)
        conn.close()

        # 构建 prompt
        prompt = build_prompt(limit=5)
        self.assertIn("AI原版", prompt)
        self.assertIn("用户修改", prompt)
        self.assertIn("用户喜欢吃面条", prompt)
        self.assertIn("兰州拉面", prompt)

        print("test_record_and_build PASS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
