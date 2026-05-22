"""AMSM 归档引擎 TDD 测试。"""

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
from memoir.archive import archive_fragment

class TestArchive(unittest.TestCase):
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
        
        # 初始化一个测试 session
        now = int(time.time())
        self.cursor.execute(
            """
            INSERT INTO sessions (
                session_id, created_at, updated_at, name, channel
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (self.session_id, now, now, "Archive Test Session", "cli")
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

    @patch('urllib.request.urlopen')
    def test_archive(self, mock_urlopen):
        # 配置 Mock API 返回
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "summary": "用户分享了他作为一个软件开发者的工作经历和对未来AI发展的看法。",
                        "keywords": ["软件开发", "AI", "未来展望"]
                    })
                }
            }]
        }).encode('utf-8')
        # __enter__ 用于支持 with 语句
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        user_content = "我是一名做了十年的软件开发者，我现在觉得AI真的发展太快了，我都怕失业。"
        assistant_content = "确实如此，AI发展迅速。但您的十年经验是AI无法替代的宝贵财富，可以多考虑如何利用AI提升效率。"
        
        # 调用归档引擎
        fragment_ids = archive_fragment(
            session_id=self.session_id,
            user_content=user_content,
            assistant_content=assistant_content,
            config=self.config
        )
        
        self.assertEqual(len(fragment_ids), 1)
        fid = fragment_ids[0]
        
        # 断言 DB 记录
        self.cursor.execute("SELECT * FROM memory_fragments WHERE fragment_id = ?", (fid,))
        row = self.cursor.fetchone()
        
        self.assertIsNotNone(row)
        self.assertEqual(row["session_id"], self.session_id)
        self.assertEqual(row["summary"], "用户分享了他作为一个软件开发者的工作经历和对未来AI发展的看法。")
        keywords = json.loads(row["keywords"])
        self.assertIn("软件开发", keywords)
        self.assertIn("AI", keywords)
        
        # 断言 FTS5 记录
        self.cursor.execute(
            "SELECT fragment_id FROM fragments_fts WHERE fragments_fts MATCH 'AI AND 软件开发'"
        )
        fts_row = self.cursor.fetchone()
        self.assertIsNotNone(fts_row)
        self.assertEqual(fts_row[0], fid)
        
        print("test_archive PASS")


if __name__ == "__main__":
    unittest.main()
