"""MemoryProvider 主入口 — AMSM 插件实现。

继承 Hermes 的 MemoryProvider ABC，实现四个核心钩子：
  initialize / prefetch / sync_turn / shutdown

签名严格按 Phase 0 从 agent/memory_provider.py 实际读取的接口定义。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import os
import sys

# 动态确保当前项目目录在 sys.path 中
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Hermes MemoryProvider 基类 — 运行时由 Hermes 注入 sys.path
from agent.memory_provider import MemoryProvider

from memoir.config import load_hermes_config
from memoir.db.conn import get_connection
from memoir.db.schema import ALL_CREATE_STATEMENTS

logger = logging.getLogger(__name__)


class AMSMProvider(MemoryProvider):
    """AMSM 记忆系统 Provider。"""

    def __init__(self):
        self._conn = None
        self._config = None

    # ---- 必须实现的 property ----

    @property
    def name(self) -> str:
        """Provider 标识符。"""
        return "amsm"

    # ---- 必须实现的抽象方法 ----

    def is_available(self) -> bool:
        """检查 AMSM 是否就绪（不做网络调用）。"""
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        """初始化会话。

        调用 load_hermes_config() 获取辅助模型配置，
        打开 DB 连接，建表（如不存在）。
        """
        hermes_home = kwargs.get("hermes_home")
        with open("/home/abel/amsm_debug.log", "a") as f:
            f.write(f"AMSMProvider.initialize called with session_id={session_id}\n")
        self._config = load_hermes_config(hermes_home)
        
        self._conn = get_connection()
        cursor = self._conn.cursor()
        for statement in ALL_CREATE_STATEMENTS:
            if statement.strip():
                cursor.execute(statement)
                
        # 确保 session 存在，否则 memory_fragments 插入会报外键错误
        if session_id:
            import time
            now = int(time.time())
            cursor.execute(
                """
                INSERT OR IGNORE INTO sessions (session_id, created_at, updated_at, name)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, now, now, "CLI_Session")
            )
            
        self._conn.commit()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """回合前召回相关上下文。"""
        from memoir.retrieval import retrieve
        with open("/home/abel/amsm_debug.log", "a") as f:
            f.write(f"AMSMProvider.prefetch called with query={query}, session_id={session_id}\n")
        if self._config is None:
            logger.warning("AMSMProvider 未初始化 config，跳过 prefetch")
            return ""
        return retrieve(query=query, session_id=session_id, config=self._config)

    def sync_turn(
        self, user_content: str, assistant_content: str, *, session_id: str = ""
    ) -> None:
        """回合完成后持久化。"""
        from memoir.archive import archive_fragment
        with open("/home/abel/amsm_debug.log", "a") as f:
            f.write(f"AMSMProvider.sync_turn called with session_id={session_id}, user={user_content}, assistant={assistant_content}\n")
        if self._config is None:
            logger.warning("AMSMProvider 未初始化 config，跳过 sync_turn")
            return
            
        fragment_ids = archive_fragment(
            session_id=session_id,
            user_content=user_content,
            assistant_content=assistant_content,
            config=self._config
        )
        from memoir.graph import update_graph
        from memoir.db.conn import get_connection
        import json
        
        conn = get_connection()
        try:
            cursor = conn.cursor()
            for fid in fragment_ids:
                row = cursor.execute("SELECT summary, keywords FROM memory_fragments WHERE fragment_id = ?", (fid,)).fetchone()
                if row:
                    summary = row[0]
                    try:
                        keywords = json.loads(row[1]) if row[1] else []
                    except:
                        keywords = []
                    try:
                        update_graph(
                            fragment_id=fid,
                            session_id=session_id,
                            summary=summary,
                            keywords=keywords,
                            config=self._config
                        )
                    except Exception as e:
                        with open("/home/abel/amsm_debug.log", "a") as f:
                            f.write(f"Graph update exception: {e}\n")
        finally:
            conn.close()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """返回此 provider 暴露的工具 schema。"""
        return []

    def shutdown(self) -> None:
        """清理退出 — 刷新队列、关闭 DB 连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None


def register(ctx: Any) -> None:
    """Hermes 插件注册入口。"""
    ctx.register_memory_provider(AMSMProvider())
