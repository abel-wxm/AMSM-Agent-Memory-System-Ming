import os
import sys
import uuid
import yaml
import time
import json

sys.path.insert(0, "/home/abel/Project/AMSM")

from memoir.retrieval import retrieve
import memoir.retrieval as ret_module
ret_module.GRAPH_TRAVERSE_MAX_DEPTH = 5
from memoir.db.conn import get_connection
from memoir.db.schema import ALL_CREATE_STATEMENTS

def load_config():
    with open('/home/abel/.hermes/config.yaml', 'r') as f:
        hermes_cfg = yaml.safe_load(f)
    
    amsm_provider_name = hermes_cfg.get('auxiliary', {}).get('amsm', {}).get('provider', '')
    if amsm_provider_name.startswith('custom:'):
        provider_name = amsm_provider_name.split('custom:')[1]
        for p in hermes_cfg.get('custom_providers', []):
            if p.get('name') == provider_name:
                key_env = p.get('api_key_env', '')
                api_key = ''
                with open('/home/abel/.hermes/.env', 'r') as ef:
                    for line in ef:
                        if line.startswith(key_env + '='):
                            api_key = line.strip().split('=', 1)[1]
                            break
                return {
                    "base_url": p.get('base_url'),
                    "model": p.get('model'),
                    "api_key": api_key,
                    "timeout": 120
                }
    return {}

def init_fresh_db():
    db_path = "/home/abel/Project/AMSM/memoir.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = get_connection(db_path)
    for stmt in ALL_CREATE_STATEMENTS:
        conn.execute(stmt)
    conn.commit()
    conn.close()

def build_mock_graph():
    init_fresh_db()
    session_id = f"test_chain_{uuid.uuid4().hex[:8]}"
    now = int(time.time())
    
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO sessions (session_id, created_at, updated_at, name, channel, is_favorite, core_tag) VALUES (?, ?, ?, ?, ?, 0, '')", (session_id, now, now, "Test", "cli"))
    
    # 构建 A -> B -> C -> D 长链路
    # A
    f_a = str(uuid.uuid4())
    n_a = str(uuid.uuid4())
    c.execute("INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, keywords, raw_text, weight, channel, archive_type, is_core, is_manual, is_public, is_favorite, last_accessed_at) VALUES (?, ?, ?, ?, '[]', ?, 1.0, 'cli', 'ai_auto', 1, 0, 0, 0, ?)", (f_a, session_id, now-4000, "用户在上海做程序员", "在上海做程序员", now-4000))
    c.execute("INSERT INTO graph_nodes (node_id, fragment_id, session_id, entity_name, entity_type, summary, status, superseded_at, superseded_reason, triggered_by_fragment_ids, valid_from, valid_until) VALUES (?, ?, ?, '上海', 'place', '用户在上海做程序员', 'superseded', ?, '跳槽去北京了', ?, ?, ?)", (n_a, f_a, session_id, now-3000, json.dumps([]), now-4000, now-3000))

    # B
    f_b = str(uuid.uuid4())
    n_b = str(uuid.uuid4())
    c.execute("INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, keywords, raw_text, weight, channel, archive_type, is_core, is_manual, is_public, is_favorite, last_accessed_at) VALUES (?, ?, ?, ?, '[]', ?, 1.0, 'cli', 'ai_auto', 0, 0, 0, 0, ?)", (f_b, session_id, now-3000, "跳槽去北京做产品经理，还在北京故宫玩了一圈", "去北京做产品经理玩故宫", now-3000))
    c.execute("INSERT INTO graph_nodes (node_id, fragment_id, session_id, entity_name, entity_type, summary, status, superseded_at, superseded_reason, triggered_by_fragment_ids, valid_from, valid_until) VALUES (?, ?, ?, '北京', 'place', '跳槽去北京做产品经理，还在北京故宫玩了一圈', 'superseded', ?, '又去大理了', ?, ?, ?)", (n_b, f_b, session_id, now-2000, json.dumps([f_a]), now-3000, now-2000))
    # A->B superseded edge
    c.execute("INSERT INTO graph_edges (edge_id, from_node_id, to_node_id, relation_type, affinity, status, created_at) VALUES (?, ?, ?, 'supersedes', 1.0, 'active', ?)", (str(uuid.uuid4()), n_b, n_a, now-3000))

    # C
    f_c = str(uuid.uuid4())
    n_c = str(uuid.uuid4())
    c.execute("INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, keywords, raw_text, weight, channel, archive_type, is_core, is_manual, is_public, is_favorite, last_accessed_at) VALUES (?, ?, ?, ?, '[]', ?, 1.0, 'cli', 'ai_auto', 0, 0, 0, 0, ?)", (f_c, session_id, now-2000, "辞职去大理开客栈，每天看苍山洱海", "大理开客栈看海", now-2000))
    c.execute("INSERT INTO graph_nodes (node_id, fragment_id, session_id, entity_name, entity_type, summary, status, superseded_at, superseded_reason, triggered_by_fragment_ids, valid_from, valid_until) VALUES (?, ?, ?, '大理', 'place', '辞职去大理开客栈，每天看苍山洱海', 'superseded', ?, '回老家了', ?, ?, ?)", (n_c, f_c, session_id, now-1000, json.dumps([f_b]), now-2000, now-1000))
    c.execute("INSERT INTO graph_edges (edge_id, from_node_id, to_node_id, relation_type, affinity, status, created_at) VALUES (?, ?, ?, 'supersedes', 1.0, 'active', ?)", (str(uuid.uuid4()), n_c, n_b, now-2000))

    # D
    f_d = str(uuid.uuid4())
    n_d = str(uuid.uuid4())
    c.execute("INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, keywords, raw_text, weight, channel, archive_type, is_core, is_manual, is_public, is_favorite, last_accessed_at) VALUES (?, ?, ?, ?, '[]', ?, 1.0, 'cli', 'ai_auto', 0, 0, 0, 0, ?)", (f_d, session_id, now-1000, "大理客栈倒闭，回老家成都做自媒体了", "回成都做自媒体", now-1000))
    c.execute("INSERT INTO graph_nodes (node_id, fragment_id, session_id, entity_name, entity_type, summary, status, superseded_at, superseded_reason, triggered_by_fragment_ids, valid_from, valid_until) VALUES (?, ?, ?, '成都', 'place', '大理客栈倒闭，回老家成都做自媒体了', 'superseded', ?, '又搬家了', ?, ?, ?)", (n_d, f_d, session_id, now-500, json.dumps([f_c]), now-1000, now-500))
    c.execute("INSERT INTO graph_edges (edge_id, from_node_id, to_node_id, relation_type, affinity, status, created_at) VALUES (?, ?, ?, 'supersedes', 1.0, 'active', ?)", (str(uuid.uuid4()), n_d, n_c, now-1000))

    # E
    f_e = str(uuid.uuid4())
    n_e = str(uuid.uuid4())
    c.execute("INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, keywords, raw_text, weight, channel, archive_type, is_core, is_manual, is_public, is_favorite, last_accessed_at) VALUES (?, ?, ?, ?, '[]', ?, 1.0, 'cli', 'ai_auto', 0, 0, 0, 0, ?)", (f_e, session_id, now-500, "自媒体没做成，去深圳卖烧脚丫子了", "深圳卖烧烤", now-500))
    c.execute("INSERT INTO graph_nodes (node_id, fragment_id, session_id, entity_name, entity_type, summary, status, superseded_at, superseded_reason, triggered_by_fragment_ids, valid_from, valid_until) VALUES (?, ?, ?, '深圳', 'place', '自媒体没做成，去深圳卖烧脚丫子了', 'active', NULL, NULL, ?, ?, ?)", (n_e, f_e, session_id, json.dumps([f_d]), now-500, now-500))
    c.execute("INSERT INTO graph_edges (edge_id, from_node_id, to_node_id, relation_type, affinity, status, created_at) VALUES (?, ?, ?, 'supersedes', 1.0, 'active', ?)", (str(uuid.uuid4()), n_e, n_d, now-500))

    # 构建一个循环边: E -> A (错误的数据导致的循环)
    c.execute("INSERT INTO graph_edges (edge_id, from_node_id, to_node_id, relation_type, affinity, status, created_at) VALUES (?, ?, ?, 'supersedes', 1.0, 'active', ?)", (str(uuid.uuid4()), n_a, n_e, now))

    # FTS
    c.execute("INSERT INTO fragments_fts (rowid, fragment_id, summary, keywords) VALUES (?, ?, ?, ?)", (1, f_a, "用户在上海做程序员之后去哪了", '["上海", "程序员"]'))
    
    conn.commit()
    conn.close()
    return session_id

def run_retrieval_sim():
    config = load_config()
    session_id = build_mock_graph()
    print(f"Created graph mock session: {session_id}")
    
    query = "我以前在上海做程序员，之后去哪了？"
    print(f"\nUser query: {query}")
    print("Calling retrieve()...")
    
    result = retrieve(query, session_id, config)
    
    print("\n--- Retrieval Output ---")
    print(result)
    print("------------------------")

if __name__ == "__main__":
    run_retrieval_sim()
