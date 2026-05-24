import os
import sys
import uuid
import yaml

# Ensure project root is in path
sys.path.insert(0, "/home/abel/Project/AMSM")

from memoir.graph import update_graph
from memoir.db.conn import get_connection

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
                # parse ~/.hermes/.env
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

def run_simulation():
    config = load_config()
    print("Loaded config:", {**config, "api_key": "***" if config.get("api_key") else ""})
    
    session_id = f"test_sim_{uuid.uuid4().hex[:8]}"
    print(f"\n--- Starting simulation for session: {session_id} ---")
    
    # 插入片段1
    frag1_id = str(uuid.uuid4())
    summary1 = "用户说他在上海做程序员，写Python后端。"
    keywords1 = ["用户", "上海", "程序员", "Python"]
    
    conn = get_connection()
    c = conn.cursor()
    # 模拟写入依赖
    now = 1716500000
    c.execute("INSERT INTO sessions (session_id, created_at, updated_at, name, channel, is_favorite, core_tag) VALUES (?, ?, ?, ?, ?, 0, '')", (session_id, now, now, "Test", "cli"))
    c.execute("INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, keywords, raw_text, weight, channel, archive_type, is_core, is_manual, is_public, is_favorite, last_accessed_at) VALUES (?, ?, ?, ?, '[]', ?, 1.0, 'cli', 'ai_auto', 0, 0, 0, 0, ?)", (frag1_id, session_id, now, summary1, summary1, now))
    conn.commit()
    conn.close()
    
    print("\n[Step 1] 用户输入: 我在上海做程序员，写Python后端")
    print(f"调用 update_graph 进行实体提取...")
    new_nodes1 = update_graph(frag1_id, session_id, summary1, keywords1, config)
    print(f"生成的节点 IDs: {new_nodes1}")
    
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT entity_name, entity_type, summary, status FROM graph_nodes WHERE node_id IN ({})".format(",".join("?" * len(new_nodes1)) if new_nodes1 else "?"), new_nodes1 if new_nodes1 else ["none"])
    nodes_info1 = c.fetchall()
    print("提取结果 (节点):")
    for row in nodes_info1:
        print("  -", dict(row))
    conn.close()
    
    # 插入片段2 (冲突)
    print("\n[Step 2] 用户输入: 我其实上个月跳槽去北京做产品经理了，不再写代码了。")
    frag2_id = str(uuid.uuid4())
    summary2 = "用户上个月跳槽去了北京做产品经理，不再做程序员写代码。"
    keywords2 = ["用户", "跳槽", "北京", "产品经理"]
    
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO memory_fragments (fragment_id, session_id, created_at, summary, keywords, raw_text, weight, channel, archive_type, is_core, is_manual, is_public, is_favorite, last_accessed_at) VALUES (?, ?, ?, ?, '[]', ?, 1.0, 'cli', 'ai_auto', 0, 0, 0, 0, ?)", (frag2_id, session_id, now+1000, summary2, summary2, now+1000))
    conn.commit()
    conn.close()
    
    print(f"调用 update_graph 提取新实体并检测冲突...")
    new_nodes2 = update_graph(frag2_id, session_id, summary2, keywords2, config)
    print(f"生成的节点 IDs: {new_nodes2}")
    
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT entity_name, entity_type, summary, status FROM graph_nodes WHERE node_id IN ({})".format(",".join("?" * len(new_nodes2)) if new_nodes2 else "?"), new_nodes2 if new_nodes2 else ["none"])
    nodes_info2 = c.fetchall()
    print("提取结果 (节点):")
    for row in nodes_info2:
        print("  -", dict(row))
        
    print("\n检查旧节点是否已被置为 superseded:")
    c.execute("SELECT entity_name, status, superseded_reason FROM graph_nodes WHERE node_id IN ({})".format(",".join("?" * len(new_nodes1)) if new_nodes1 else "?"), new_nodes1 if new_nodes1 else ["none"])
    for row in c.fetchall():
        print("  -", dict(row))
        
    print("\n检查是否生成了 supersedes 边:")
    c.execute("SELECT from_node_id, to_node_id, relation_type FROM graph_edges WHERE relation_type = 'supersedes' AND from_node_id IN ({})".format(",".join("?" * len(new_nodes2)) if new_nodes2 else "?"), new_nodes2 if new_nodes2 else ["none"])
    for row in c.fetchall():
        print("  - 边:", dict(row))
        
    conn.close()
    print("\n--- Simulation Complete ---")

if __name__ == "__main__":
    run_simulation()
