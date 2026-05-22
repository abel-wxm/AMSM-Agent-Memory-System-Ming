"""7 张表 + 1 张 FTS5 虚拟表的建表 SQL 常量。

表清单（SPEC v6.0）：
  1. memory_fragments — 核心主表（raw_text 永不修改，H-3）
  2. sessions — 会话元数据
  3. graph_nodes — 时序知识图谱节点
  4. graph_edges — 图谱关系
  5. mutation_log — 图谱变更日志（只 INSERT，H-4）
  6. session_graph_index — 跨会话关联权重
  7. global_graph_index — 总表记忆结构图
  8. fragments_fts — FTS5 全文检索虚拟表
  9. evolution_samples — 自进化学习样本
"""

# -- 1. memory_fragments ------------------------------------------------
SQL_CREATE_MEMORY_FRAGMENTS = """
CREATE TABLE IF NOT EXISTS memory_fragments (
    fragment_id          TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL,
    created_at           INTEGER NOT NULL,
    summary              TEXT,
    keywords             TEXT,
    raw_text             TEXT,
    weight               REAL DEFAULT 1.0,
    is_manual            INTEGER DEFAULT 0,
    is_core              INTEGER DEFAULT 0,
    core_reason          TEXT,
    is_public            INTEGER DEFAULT 0,
    is_favorite          INTEGER DEFAULT 0,
    archive_type         TEXT,
    channel              TEXT,
    source_msg_ids       TEXT,
    public_source_path   TEXT,
    modified_log         TEXT,
    last_accessed_at     INTEGER NOT NULL,
    gc_deleted_at        INTEGER,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
"""

# -- 2. sessions --------------------------------------------------------
SQL_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id           TEXT PRIMARY KEY,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    name                 TEXT DEFAULT '未命名会话',
    channel              TEXT,
    core_tag             TEXT,
    is_favorite          INTEGER DEFAULT 0,
    summary              TEXT,
    hermes_version       TEXT,
    fast_lane_sessions   TEXT
);
"""

# -- 3. graph_nodes -----------------------------------------------------
SQL_CREATE_GRAPH_NODES = """
CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id                      TEXT PRIMARY KEY,
    fragment_id                  TEXT NOT NULL,
    session_id                   TEXT NOT NULL,
    entity_name                  TEXT,
    entity_type                  TEXT,
    summary                      TEXT,
    status                       TEXT DEFAULT 'active',
    valid_from                   INTEGER NOT NULL,
    valid_until                  INTEGER,
    superseded_at                INTEGER,
    superseded_reason            TEXT,
    triggered_by_fragment_ids    TEXT,
    gc_pending                   INTEGER DEFAULT 0,
    gc_pending_since             INTEGER,
    FOREIGN KEY(fragment_id) REFERENCES memory_fragments(fragment_id),
    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
"""

# -- 4. graph_edges -----------------------------------------------------
SQL_CREATE_GRAPH_EDGES = """
CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id          TEXT PRIMARY KEY,
    from_node_id     TEXT NOT NULL,
    to_node_id       TEXT NOT NULL,
    relation_type    TEXT NOT NULL,
    affinity         REAL DEFAULT 0.5,
    created_at       INTEGER NOT NULL,
    status           TEXT DEFAULT 'active',
    FOREIGN KEY(from_node_id) REFERENCES graph_nodes(node_id),
    FOREIGN KEY(to_node_id) REFERENCES graph_nodes(node_id)
);
"""

# -- 5. mutation_log ----------------------------------------------------
SQL_CREATE_MUTATION_LOG = """
CREATE TABLE IF NOT EXISTS mutation_log (
    log_id                  TEXT PRIMARY KEY,
    changed_at              INTEGER NOT NULL,
    node_id                 TEXT NOT NULL,
    old_status              TEXT,
    new_status              TEXT NOT NULL,
    reason                  TEXT,
    triggered_by_fragment   TEXT
);
"""

# -- 6. session_graph_index ---------------------------------------------
SQL_CREATE_SESSION_GRAPH_INDEX = """
CREATE TABLE IF NOT EXISTS session_graph_index (
    from_session_id   TEXT NOT NULL,
    to_session_id     TEXT NOT NULL,
    session_affinity  INTEGER DEFAULT 0,
    last_hit_at       INTEGER NOT NULL,
    PRIMARY KEY (from_session_id, to_session_id),
    FOREIGN KEY(from_session_id) REFERENCES sessions(session_id),
    FOREIGN KEY(to_session_id) REFERENCES sessions(session_id)
);
"""

# -- 7. global_graph_index ----------------------------------------------
SQL_CREATE_GLOBAL_GRAPH_INDEX = """
CREATE TABLE IF NOT EXISTS global_graph_index (
    global_node_id    TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL,
    summary           TEXT,
    keywords          TEXT,
    timestamps        TEXT,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL,
    last_summarized_at INTEGER DEFAULT 0,
    summarize_count    INTEGER DEFAULT 0,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
"""

# -- 8. fragments_fts (FTS5 虚拟表) ------------------------------------
SQL_CREATE_FRAGMENTS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS fragments_fts USING fts5(
    fragment_id UNINDEXED,
    summary,
    keywords,
    content='memory_fragments',
    content_rowid='rowid'
);
"""

# -- 9. evolution_samples -----------------------------------------------
SQL_CREATE_EVOLUTION_SAMPLES = """
CREATE TABLE IF NOT EXISTS evolution_samples (
    sample_id     TEXT PRIMARY KEY,
    created_at    INTEGER NOT NULL,
    ai_version    TEXT,
    user_version  TEXT,
    diff_score    REAL,
    weight        REAL DEFAULT 1.0
);
"""

# -- 全部建表语句列表（供 init_db 使用）---------------------------------
ALL_CREATE_STATEMENTS = [
    SQL_CREATE_SESSIONS,
    SQL_CREATE_MEMORY_FRAGMENTS,
    SQL_CREATE_GRAPH_NODES,
    SQL_CREATE_GRAPH_EDGES,
    SQL_CREATE_MUTATION_LOG,
    SQL_CREATE_SESSION_GRAPH_INDEX,
    SQL_CREATE_GLOBAL_GRAPH_INDEX,
    SQL_CREATE_FRAGMENTS_FTS,
    SQL_CREATE_EVOLUTION_SAMPLES,
]
