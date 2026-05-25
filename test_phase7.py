import sqlite3
import os
import sys

sys.path.insert(0, '/home/abel/Project/AMSM')
from memoir.atomic import run_atomic_extraction
from memoir.db.conn import get_connection
import time
import uuid

db_path = os.path.expanduser('~/.hermes/memory.db')
os.environ["AMSM_DB_PATH"] = db_path
conn = get_connection()
# Clean up public_library
conn.execute("DELETE FROM public_library")

# Insert fake is_atomic=0 record
conn.execute(
    """
    INSERT INTO public_library (public_id, source_fragment_id, raw_text, summary, keywords, added_by, created_at, is_atomic)
    VALUES (?, 'frag1', 'User likes dogs', 'likes dogs', '["dog"]', 'test', ?, 0)
    """,
    (str(uuid.uuid4()), int(time.time()))
)
conn.commit()

config = {"base_url": ""} # Mock mode will just return [raw_text]

count = run_atomic_extraction(config)

print(f"Extracted atoms: {count}")

cursor = conn.cursor()
cursor.execute("SELECT is_atomic FROM public_library")
rows = cursor.fetchall()
# Should have one is_atomic=2, and one is_atomic=1
assert len(rows) == 2, "Should have 2 rows total"
states = set(r["is_atomic"] for r in rows)
assert states == {1, 2}, "Should have one processed (2) and one new atom (1)"

print("Phase 7 test passed!")
