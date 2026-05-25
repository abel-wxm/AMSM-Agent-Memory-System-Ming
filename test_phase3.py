import sqlite3
import os
import sys

sys.path.insert(0, '/home/abel/Project/AMSM')
from memoir.archive import archive_fragment
from memoir.db.conn import get_connection

# 1. 简单重置 db
db_path = os.path.expanduser('~/.hermes/memory.db')
conn = get_connection()
conn.execute("DELETE FROM fragments_fts")
conn.execute("DELETE FROM fragment_tags")
conn.execute("DELETE FROM memory_fragments")
conn.commit()

session_id = "test_session_1"
conn.execute("INSERT OR IGNORE INTO sessions (session_id, created_at, updated_at) VALUES (?, 0, 0)", (session_id,))
conn.commit()

config = {"base_url": "", "model": "mock"}

# 2. 测试静默期
print("Testing silent period...")
fids_1 = archive_fragment(session_id, "User1", "Assistant1", config)
assert len(fids_1) == 0, "静默期应该返回 []，不创建任何节点"

# 强行塞入 20 条，打破静默期
for _ in range(20):
    conn.execute(
        "INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, keywords, raw_text, weight, last_accessed_at) VALUES (lower(hex(randomblob(16))), ?, 0, '', '', 'a', 1.0, 0)",
        (session_id,)
    )
conn.commit()

# 3. 测试非静默期归档与 FTS 索引
print("Testing active period archive & FTS5...")
# 这里使用 mock，所以 _call_llm_for_archive 会返回 text[:40] 和 ["无配置"]
fids_2 = archive_fragment(session_id, "Hello this is a test", "I understand the test", config)
assert len(fids_2) > 0, "非静默期应该返回有效 id"

# 检查 FTS
cursor = conn.cursor()
cursor.execute("SELECT fragment_id FROM fragments_fts WHERE fragments_fts MATCH 'test'")
rows = cursor.fetchall()
assert len(rows) > 0, "FTS5 应该能匹配到刚插入的内容"
assert rows[0]['fragment_id'] == fids_2[0], "FTS5 的 fragment_id 匹配正确"

print("All Phase 3 tests passed!")
