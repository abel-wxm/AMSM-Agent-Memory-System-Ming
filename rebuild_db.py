import sqlite3
import os
import sys

sys.path.insert(0, '/home/abel/Project/AMSM')
from memoir.db.conn import get_connection
from memoir.db.schema import ALL_CREATE_STATEMENTS

db_path = os.path.expanduser('~/.hermes/memory.db')
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS public_library;")
    conn.commit()
    conn.close()

conn = get_connection(db_path)
for statement in ALL_CREATE_STATEMENTS:
    conn.execute(statement)
conn.commit()
conn.close()
print("Database schema upgraded successfully.")
