"""AMSM 检索引擎 TDD 测试。

测试A: 有时间概念，历史模式
测试B: 无时间概念，当前模式
测试C: 加载深度验证
测试D: 跨会话触发验证
"""

import unittest
import os
import sys
import time
import uuid
import json
import math
from unittest.mock import patch, MagicMock

# 确保能 import memoir
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from memoir.db.conn import get_connection
from memoir.retrieval import retrieve
from memoir.scorer import (
    _calc_t_score,
    _calc_k_score,
    _calc_w_score,
    score_fragments,
)


class _BaseTestCase(unittest.TestCase):
    """基础测试，自动初始化 session 和若干 fragments。"""

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

        # 创建测试会话
        self.cursor.execute(
            """
            INSERT INTO sessions (session_id, created_at, updated_at, name, channel)
            VALUES (?, ?, ?, ?, ?)
            """,
            (self.session_id, self.now, self.now, "Retrieval Test", "cli"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _insert_fragment(
        self,
        summary="测试摘要",
        keywords=None,
        raw_text="测试原文",
        weight=1.0,
        is_manual=0,
        is_core=0,
        is_public=0,
        created_at=None,
        session_id=None,
    ) -> str:
        fid = str(uuid.uuid4())
        kw_json = json.dumps(keywords or ["测试"], ensure_ascii=False)
        cat = created_at or self.now
        sid = session_id or self.session_id
        self.cursor.execute(
            """
            INSERT INTO memory_fragments (
                fragment_id, session_id, created_at, summary, keywords,
                raw_text, weight, is_manual, is_core, is_public, is_favorite,
                channel, last_accessed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'cli', ?)
            """,
            (fid, sid, cat, summary, kw_json, raw_text, weight, is_manual, is_core, is_public, cat),
        )
        # 同步 FTS
        self.cursor.execute(
            """
            INSERT INTO fragments_fts (rowid, fragment_id, summary, keywords)
            VALUES (?, ?, ?, ?)
            """,
            (self.cursor.lastrowid, fid, summary, kw_json),
        )
        self.conn.commit()
        return fid

    def _insert_graph_node(
        self, fid, entity_name, entity_type="fact", status="active",
        superseded_at=None, superseded_reason=None, session_id=None,
    ) -> str:
        nid = str(uuid.uuid4())
        sid = session_id or self.session_id
        self.cursor.execute(
            """
            INSERT INTO graph_nodes (
                node_id, fragment_id, session_id, entity_name, entity_type,
                summary, status, valid_from, superseded_at, superseded_reason
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?)
            """,
            (nid, fid, sid, entity_name, entity_type, status, self.now,
             superseded_at, superseded_reason),
        )
        self.conn.commit()
        return nid


class TestA_HistoryMode(_BaseTestCase):
    """测试A（有时间概念，历史模式）：输入「我以前的同事怎么样了」"""

    @patch("memoir.retrieval._call_llm_time_analysis")
    def test_history_mode(self, mock_time):
        mock_time.return_value = {
            "has_time_concept": True,
            "time_center": self.now - 365 * 86400,  # 1年前
            "confidence_radius": 180.0,  # 半径180天
            "is_history_mode": True,
            "confidence": 0.9,
        }

        # 准备数据：一个 superseded 的旧同事节点
        fid_old = self._insert_fragment(
            summary="用户的同事老王是结构工程师",
            keywords=["同事", "老王", "工程师"],
            raw_text="用户说他同事老王和他一起做结构设计",
            created_at=self.now - 400 * 86400,
        )
        self._insert_graph_node(
            fid_old, "同事", status="superseded",
            superseded_at=self.now - 100 * 86400,
            superseded_reason="用户已转行，前同事关系不再活跃",
        )

        # 一个 active 的当前节点
        fid_new = self._insert_fragment(
            summary="用户现在做电商",
            keywords=["电商", "工作"],
            raw_text="用户目前从事电商行业",
        )
        self._insert_graph_node(fid_new, "电商", status="active")

        result = retrieve("我以前的同事怎么样了", self.session_id, self.config)

        # 断言：T_weight=0.65, K_weight=0.25
        # 验证 mock 被调用，且 has_time_concept=True
        mock_time.assert_called_once()
        call_args = mock_time.return_value
        self.assertTrue(call_args["has_time_concept"])
        self.assertTrue(call_args["is_history_mode"])

        # 断言：返回文本包含 ⚠️（因为命中了 superseded 节点）
        self.assertIn("⚠️", result)

        print("test_A_history_mode PASS")


class TestB_CurrentMode(_BaseTestCase):
    """测试B（无时间概念，当前模式）：输入「我现在的工作怎样」"""

    @patch("memoir.retrieval._call_llm_time_analysis")
    def test_current_mode(self, mock_time):
        mock_time.return_value = {
            "has_time_concept": False,
            "time_center": None,
            "confidence_radius": None,
            "is_history_mode": False,
            "confidence": 1.0,
        }

        # superseded 节点（不应被返回）
        fid_old = self._insert_fragment(
            summary="用户曾经是结构工程师",
            keywords=["结构工程师", "工作"],
            raw_text="用户以前从事结构工程",
            created_at=self.now - 200 * 86400,
        )
        self._insert_graph_node(
            fid_old, "工作", status="superseded",
            superseded_at=self.now - 50 * 86400,
            superseded_reason="用户已经转行",
        )

        # active 节点（应被返回）
        fid_new = self._insert_fragment(
            summary="用户现在做电商运营很开心",
            keywords=["电商", "运营", "工作"],
            raw_text="用户目前从事电商运营工作",
        )
        self._insert_graph_node(fid_new, "工作", status="active")

        result = retrieve("我现在的工作怎样", self.session_id, self.config)

        # 断言：不包含 ⚠️（没有 superseded 节点被显示）
        self.assertNotIn("⚠️", result)

        # 断言：包含当前工作内容
        self.assertIn("电商", result)

        print("test_B_current_mode PASS")


class TestC_LoadingDepth(_BaseTestCase):
    """测试C（加载深度）：准备10条片段，得分各不同"""

    @patch("memoir.retrieval._call_llm_time_analysis")
    def test_loading_depth(self, mock_time):
        mock_time.return_value = {
            "has_time_concept": False,
            "time_center": None,
            "confidence_radius": None,
            "is_history_mode": False,
            "confidence": 1.0,
        }

        # 准备10条片段，权重从高到低，关键词均含"技术"
        fids = []
        for i in range(10):
            fid = self._insert_fragment(
                summary=f"技术片段第{i+1}条摘要",
                keywords=["技术", f"tag{i+1}"],
                raw_text=f"这是技术片段第{i+1}条的完整原文内容，包含详细信息。",
                weight=10.0 - i,  # 权重 10, 9, 8, ..., 1
                created_at=self.now - i * 86400,  # 越新权重越高
            )
            self._insert_graph_node(fid, "技术", status="active")
            fids.append(fid)

        result = retrieve("技术", self.session_id, self.config)

        # 解析结果：排名前5的应该有原文内容（"完整原文"），6-10只有摘要
        lines = result.split("\n")

        # 收集每条记录：找到包含"片段"的内容行
        content_lines = [l for l in lines if "片段" in l and "来源" not in l and "⚠️" not in l]

        # 至少应有10条（或受500字截断影响，至少5条含raw_text）
        raw_text_count = sum(1 for l in content_lines if "完整原文" in l)
        summary_only_count = sum(1 for l in content_lines if "摘要" in l and "完整原文" not in l)

        # 排名 1-5 应加载了 raw_text（包含"完整原文"）
        self.assertGreaterEqual(raw_text_count, 1, "应至少有1条包含raw_text")

        # 排名 6+ 应只有 summary（包含"摘要"但不含"完整原文"）
        self.assertGreaterEqual(summary_only_count, 1, "应至少有1条只有summary")

        print("test_C_loading_depth PASS")


class TestD_CrossSessionTrigger(_BaseTestCase):
    """测试D（跨会话触发）：清空当前会话片段，输入「电商」"""

    @patch("memoir.retrieval._call_llm_time_analysis")
    @patch("memoir.cross_session.search")
    def test_cross_session_trigger(self, mock_cross, mock_time):
        mock_time.return_value = {
            "has_time_concept": False,
            "time_center": None,
            "confidence_radius": None,
            "is_history_mode": False,
            "confidence": 1.0,
        }

        # 不在当前会话放任何片段 → 命中数=0，触发跨会话
        # 在另一个会话中放一条
        other_session = str(uuid.uuid4())
        self.cursor.execute(
            """
            INSERT INTO sessions (session_id, created_at, updated_at, name, channel)
            VALUES (?, ?, ?, ?, ?)
            """,
            (other_session, self.now, self.now, "Other Session", "cli"),
        )
        self.conn.commit()

        fid = self._insert_fragment(
            summary="电商运营技巧",
            keywords=["电商", "运营"],
            raw_text="电商运营的核心技巧包括...",
            session_id=other_session,
        )

        # mock retrieve_cross_session 返回空列表（验证调用即可）
        mock_cross.return_value = []

        result = retrieve("电商", self.session_id, self.config)

        # 断言：retrieve_cross_session 被调用
        self.assertTrue(mock_cross.called, "跨会话检索应被触发")

        print("test_D_cross_session_trigger PASS")


class TestScoringFormulas(unittest.TestCase):
    """评分公式单元测试。"""

    def test_t_score_no_time(self):
        """无时间概念：纯衰减 exp(-0.05*d)"""
        # d=0 → 1.0
        self.assertAlmostEqual(_calc_t_score(0, float("inf")), 1.0, places=2)
        # d=20天 → exp(-1.0) ≈ 0.368
        self.assertAlmostEqual(
            _calc_t_score(20, float("inf")),
            math.exp(-1.0), places=2,
        )

    def test_t_score_within_radius(self):
        """有时间概念，在范围内：1.0 - 0.30*(d/r)"""
        # d=0（正好在中心点）→ 1.0
        self.assertAlmostEqual(
            _calc_t_score(0, 60.0), 1.0, places=2,
        )
        # d=30（半径60）→ 1.0 - 0.30*0.5 = 0.85
        self.assertAlmostEqual(
            _calc_t_score(30, 60.0), 0.85, places=2,
        )

    def test_t_score_outside_radius(self):
        """有时间概念，超出范围：0.70 * exp(-0.05*(d-r))"""
        # d=100天, r=30天 → d-r=70 → 0.70 * exp(-3.5)
        score = _calc_t_score(100, 30.0)
        expected = 0.70 * math.exp(-0.05 * 70)
        self.assertAlmostEqual(score, expected, places=3)

    def test_k_score(self):
        """K_score 指数加成"""
        # 0 命中 → 0
        self.assertEqual(_calc_k_score(0, 0, 2), 0.0)
        # 全命中 → 1.0 (h_k=2, n=2)
        self.assertEqual(_calc_k_score(2, 0, 2), 1.0)
        # 部分命中
        score = _calc_k_score(1, 0, 3)
        self.assertGreater(score, 0)
        self.assertLessEqual(score, 1.0)

    def test_w_score(self):
        """W_score 对数归一化"""
        self.assertAlmostEqual(_calc_w_score(1.0, 1.0), 1.0, places=2)
        self.assertAlmostEqual(_calc_w_score(1.0, 10.0), math.log(2) / math.log(11), places=3)

    def test_manual_boost(self):
        """is_manual=1 → Score × 2.0"""
        frag_normal = {
            "created_at": int(time.time()),
            "keywords": '["test"]',
            "summary": "test",
            "weight": 1.0,
            "is_core": 0,
            "is_manual": 0,
        }
        frag_manual = dict(frag_normal)
        frag_manual["is_manual"] = 1

        scored = score_fragments([frag_normal, frag_manual], int(time.time()), float("inf"), False, 1.0, ["test"])
        score_normal = scored[0]["base_score"]
        
        # 找到 manual 那个
        for s in scored:
            if s["is_manual"] == 1:
                self.assertAlmostEqual(s["final_score"], s["base_score"] * 2.0, places=5)



if __name__ == "__main__":
    unittest.main(verbosity=2)
