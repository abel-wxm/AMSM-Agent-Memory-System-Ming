"""数据库连接管理。

使用 ~/hermes-env/ 的 Python 内置 sqlite3。
打开 memoir.db，设置 WAL 模式和外键约束。
不加载 sqlite-vec（禁止使用）。
"""

from __future__ import annotations

import sqlite3
import os
from typing import Optional

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取 memoir.db 数据库连接。

    Parameters
    ----------
    db_path : str, optional
        数据库文件路径。若未指定，使用项目目录下的 memoir.db。

    Returns
    -------
    sqlite3.Connection
        已配置 WAL 模式和外键约束的连接。
    """
    if db_path is None:
        db_path = os.environ.get("AMSM_DB_PATH")
        if not db_path:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))
            db_path = os.path.join(project_root, "memoir.db")
        
    conn = sqlite3.connect(db_path)
    # Enable WAL mode for better concurrency
    conn.execute("PRAGMA journal_mode=WAL")
    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys=ON")
    
    # Return rows as sqlite3.Row for dict-like access
    conn.row_factory = sqlite3.Row
    return conn
