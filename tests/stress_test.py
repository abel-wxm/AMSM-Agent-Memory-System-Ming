import os
import sys
import time
import uuid
import random
import string

sys.path.insert(0, '/home/abel/Project/AMSM')
from memoir.db.conn import get_connection
from memoir.db.schema import ALL_CREATE_STATEMENTS
from memoir.retrieval import retrieve

def generate_random_string(length):
    return ''.join(random.choices(string.ascii_letters + string.digits + ' ', k=length))

def run_stress_test(num_records=5000):
    print(f"Starting stress test with {num_records} records...")
    db_path = f"/tmp/amsm_stress_{uuid.uuid4()}.db"
    os.environ["AMSM_DB_PATH"] = db_path
    
    conn = get_connection()
    cursor = conn.cursor()
    for stmt in ALL_CREATE_STATEMENTS:
        if stmt.strip():
            cursor.execute(stmt)
            
    session_id = str(uuid.uuid4())
    now = int(time.time())
    cursor.execute("INSERT INTO sessions (session_id, created_at, updated_at) VALUES (?, ?, ?)", (session_id, now, now))
    
    print("Inserting data...")
    start_insert = time.time()
    
    # Batch insert to speed up setup
    batch_size = 500
    for i in range(0, num_records, batch_size):
        fragments = []
        fts = []
        tags = []
        for j in range(batch_size):
            fid = str(uuid.uuid4())
            word = "target_keyword" if random.random() < 0.05 else "random"
            summary = f"{generate_random_string(20)} {word}"
            keywords = f'["{word}", "test"]'
            raw_text = generate_random_string(200)
            fragments.append((fid, session_id, now - random.randint(0, 10000), summary, keywords, raw_text, 1.0, now))
            fts.append((fid, summary, keywords))
            
            # random tags
            if random.random() < 0.1:
                tags.append((fid, 'core', '1', now))
            if random.random() < 0.05:
                tags.append((fid, 'identity_fact', '1', now))
                
        cursor.executemany("INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, keywords, raw_text, weight, last_accessed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", fragments)
        cursor.executemany("INSERT INTO fragments_fts (fragment_id, summary, keywords) VALUES (?, ?, ?)", fts)
        if tags:
            cursor.executemany("INSERT INTO fragment_tags (fragment_id, tag_type, tag_value, created_at) VALUES (?, ?, ?, ?)", tags)
            
    conn.commit()
    insert_duration = time.time() - start_insert
    print(f"Inserted {num_records} records in {insert_duration:.2f} seconds.")
    
    print("Testing retrieval speed...")
    config = {"base_url": "", "model": "mock", "api_key": "mock"}
    
    start_retrieve = time.time()
    # retrieve calls preprocess which calls time analysis LLM, but our base_url is "" so it's instant
    result = retrieve("target_keyword", session_id, config)
    retrieve_duration = time.time() - start_retrieve
    
    print(f"Retrieval took {retrieve_duration:.3f} seconds.")
    print(f"Result length: {len(result)} characters.")
    assert retrieve_duration < 1.0, "Retrieval is too slow!"
    assert "target_keyword" in result, "Did not retrieve target keyword."
    
    conn.close()
    if os.path.exists(db_path):
        os.remove(db_path)
    print("Stress test PASSED!")

if __name__ == "__main__":
    run_stress_test(5000)
