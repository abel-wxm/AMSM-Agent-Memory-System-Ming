import os
import json
from memoir.db.conn import get_connection
from memoir.db.schema import ALL_CREATE_STATEMENTS
from memoir.archive import archive_fragment
from memoir.retrieval import retrieve

# Initialize DB
conn = get_connection()
cursor = conn.cursor()
for stmt in ALL_CREATE_STATEMENTS:
    cursor.execute(stmt)
conn.commit()

# Ensure session exists
cursor.execute("INSERT OR IGNORE INTO sessions (session_id, created_at, updated_at, name) VALUES ('test_sess', 1000, 1000, 'Test Session')")
conn.commit()
conn.close()

# Insert fragment with public memory
config = {} # use default mocks
tags = {
    "is_public": "1",
    "core": "user_specified",
    "favorite": "1"
}

print("Running archive_fragment...")
fids = archive_fragment("test_sess", "什么是量子力学？", "量子力学是描述微观世界的物理学分支。", config, tags=tags)
print(f"Archived fragment IDs: {fids}")

# Check DB
conn = get_connection()
c = conn.cursor()
c.execute("SELECT * FROM memory_fragments WHERE fragment_id=?", (fids[0],))
mf = dict(c.fetchone())
print("memory_fragments:", mf)

c.execute("SELECT * FROM public_library")
pl = dict(c.fetchone())
print("public_library:", pl)

c.execute("SELECT * FROM fragment_tags")
for row in c.fetchall():
    print("fragment_tags:", dict(row))
conn.close()

# Test retrieve
print("\nRunning retrieve...")
ret = retrieve("量子", "test_sess", config)
print(ret)
