#!/usr/bin/env python3
"""数据库初始化脚本。

执行 memoir/db/schema.py 中的 ALL_CREATE_STATEMENTS，
在 ~/Project/AMSM/memoir.db 中建表。
"""

import sys
import os

# 确保能 import memoir
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from memoir.db.conn import get_connection
from memoir.db.schema import ALL_CREATE_STATEMENTS

def main():
    print("正在初始化 memoir.db ...")
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for statement in ALL_CREATE_STATEMENTS:
            if statement.strip():
                cursor.execute(statement)
        conn.commit()
        print("数据库初始化成功，所有表已创建。")
    except Exception as e:
        print(f"数据库初始化失败: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
