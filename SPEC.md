# AMSM SPEC v6.4 — Agent 执行手册
# 每次任务开始前必须完整阅读本文件

项目名称：AMSM（Agent Memory System Ming）
项目路径：~/AMSM/
数据库：~/AMSM/memoir.db（SQLite，WAL模式）
虚拟环境：~/hermes-env/（复用Hermes自带，不新建）
IDE：Google Antigravity 2.0（Windows宿主机），代码在WSL Ubuntu 22.04执行

---

## 一、H系列约束（最高优先级，任何情况不得违反）

### 数据完整性约束
H-1  每次开始前：去Hermes实际安装目录读取当前版本的
     MemoryProvider接口签名，禁止凭记忆编写钩子参数
H-2  status=superseded的节点在当前模式下禁止注入上下文
H-3  memory_fragments.raw_text字段永不修改，
     禁止任何UPDATE操作
H-4  mutation_log只允许INSERT，禁止UPDATE和DELETE
H-5  is_core字段只能由用户操作或明确的AI分类逻辑设置，
     禁止随机赋值或批量赋值
H-6  prefetch返回片段上限20条，超出时按得分截断
H-7  Hermes版本升级后，必须重新验证钩子签名再继续开发

### 执行行为约束
H-8  不得假设任何文件存在，每次使用文件前必须先验证路径
H-9  不得跳步，每个Phase完成验证后才能进入下一步
H-10 遇到预期外的错误，立即STOP，
     输出完整错误信息，等待人类指令

---

## 二、外部依赖约束（零额外依赖原则）

**本系统零额外Python依赖**，全部复用 hermes-env 已有包：

| 功能 | 实现方式 | 依赖 |
|------|----------|------|
| 数据存储 | SQLite | Python内置 sqlite3 |
| 关键词全文检索 | SQLite FTS5虚拟表 | SQLite内置，无需安装 |
| 模型调用（LLM/Embedding） | 运行时读取Hermes配置，调用其辅助模型API | 复用Hermes已有配置 |
| UUID生成 | Python内置 uuid | Python内置 |
| JSON处理 | Python内置 json | Python内置 |

**明确禁止：**
- 禁止安装 sqlite-vec（向量检索，本系统不需要）
- 禁止安装 rank-bm25（本系统用FTS5替代，无需此包）
- 禁止安装 LangChain、LlamaIndex等重型框架
- 禁止在代码中硬编码任何模型名、API地址、端口号

**模型调用规则：**
- `config.py` 在 initialize 钩子执行时动态读取 Hermes 配置文件
- 读取 Hermes 辅助模型对应的 API endpoint 和 model name（字段名需Phase 0实际确认）
- 所有 LLM 调用（生成summary/keywords/图谱分析）均通过 config.py 取到的值
- Hermes 配置变更后，下次启动自动生效，无需修改 AMSM 代码

---

## 三、数据库：7张表

> v6.0 在 v5.0 基础上新增：session_graph_index（跨会话关联权重）、global_graph_index（总表记忆结构图）；graph_nodes新增过时标记字段；memory_fragments新增GC追踪字段

### memory_fragments（核心主表，永不修改raw_text）
```
fragment_id          TEXT PK   UUID，全局唯一
session_id           TEXT FK   关联sessions表
created_at           INTEGER   Unix时间戳（精确到秒）
summary              TEXT      AI摘要，50字以内
keywords             TEXT      JSON数组，3-5个关键词
raw_text             TEXT      原始文本，永不修改（H-3）
weight               REAL      AI总结默认1.0，用户指定默认2.0（可手动调整，用于AI学习优先级）
is_manual            INTEGER   0=AI自动，1=人工总结
is_core              INTEGER   0=普通，1=核心（prefetch优先加载）
core_reason          TEXT      user_bookmarked/ai_classified/user_specified
is_public            INTEGER   0=仅本会话，1=已归档至公共库
is_favorite          INTEGER   0=未收藏，1=已收藏
archive_type         TEXT      ai_auto/manuel/user_specified/user_favorite
channel              TEXT      来源通道 cli/webui/wechat/other
source_msg_ids       TEXT      原始消息ID列表（JSON数组）
public_source_path   TEXT      公共库溯源路径（若已同步）
modified_log         TEXT      修改记录（JSON数组）
last_accessed_at     INTEGER   最后一次被检索命中的时间戳（GC判断依据，初始=created_at）
```

### sessions（会话元数据）
```
session_id           TEXT PK
created_at           INTEGER
updated_at           INTEGER
name                 TEXT      默认「未命名会话」
channel              TEXT
core_tag             TEXT      会话核心标签（动态更新）
is_favorite          INTEGER
summary              TEXT      本次会话一句话摘要
hermes_version       TEXT      记录当时Hermes版本
fast_lane_sessions   TEXT      快速通道目标会话ID列表（JSON数组），直接跳过总表/遍历
```

### graph_nodes（时序知识图谱节点）

设计原则：graph_nodes 只存图谱逻辑字段和指针，不复制 memory_fragments 的内容字段。
所有内容（summary/keywords/raw_text）通过 fragment_id 回表从 memory_fragments 取。
entity_name/entity_type 是唯二允许冗余存储的字段，原因是图谱检索入口必须在此表，
否则每次检索都要全表扫 memory_fragments。

```
node_id                      TEXT PK   UUID
fragment_id                  TEXT FK   指向memory_fragments（唯一指针，不复制内容）
session_id                   TEXT FK   所属会话（用于按会话隔离图谱检索）
entity_name                  TEXT      实体名称（冗余存储，图谱检索入口必须，其余不冗余）
entity_type                  TEXT      person/place/fact/concept/skill（同上）
status                       TEXT      active / superseded
valid_from                   INTEGER   事实生效时间戳
valid_until                  INTEGER   事实失效时间戳，NULL=至今有效
superseded_at                INTEGER   NULL=未过时；非NULL=被标记过时的时间戳
superseded_reason            TEXT      过时原因（AI一句话说明）
triggered_by_fragment_ids    TEXT      触发过时标记的fragment_id列表（JSON数组）
gc_pending                   INTEGER   0=正常；1=fragment已进入GC第一阶段
gc_pending_since             INTEGER   gc_pending置1的时间戳
```

⚠️ 禁止在 graph_nodes 中复制 summary/keywords/raw_text 字段。
   需要这些内容时，通过 fragment_id JOIN memory_fragments 取得。

### graph_edges（图谱关系）
```
edge_id          TEXT PK   UUID
from_node_id     TEXT FK
to_node_id       TEXT FK
relation_type    TEXT      见下方合法值列表
affinity         REAL      关系权重0.0-1.0，默认0.5
created_at       INTEGER
status           TEXT      active/superseded
```

合法的 relation_type 值（只能用这些，不可自造）：
```
supersedes        新事实取代旧事实
implies_outdated  间接导致关联事实过时
related_to        相关但无直接替代
belongs_to        从属/归类关系
works_at          工作关系
knows             人际关系
lives_in          居住关系
```

affinity赋值规则：
- 频繁提及关系 = 0.8+
- 偶尔提及 = 0.5
- 系统默认 = 0.5

### mutation_log（图谱变更日志，只增不改）
```
log_id                  TEXT PK   UUID
changed_at              INTEGER   变更发生时间
node_id                 TEXT      被变更的节点ID
old_status              TEXT      变更前状态
new_status              TEXT      变更后状态
reason                  TEXT      AI生成的变更原因（一句话）
triggered_by_fragment   TEXT      触发本次变更的新片段ID
```

### session_graph_index（跨会话关联权重表）
```
from_session_id   TEXT FK   发起检索的会话ID（单向记录，A→B不代表B→A）
to_session_id     TEXT FK   被检索的目标会话ID
session_affinity  INTEGER   关联度，取值范围 -20 到 +20，初始值0
last_updated      INTEGER   最近一次更新时间戳

PRIMARY KEY (from_session_id, to_session_id)
```

session_affinity 更新规则：
- 由大模型判断用户反馈情感强度，分三档：
  轻度（mild）：base_delta = 1
  明确（clear）：base_delta = 2
  强烈（strong）：base_delta = 3
- 正向（用户肯定）：delta = +base_delta
- 负向（用户否定）：delta = -base_delta
- |session_affinity| > 10 时：delta 翻倍（1→2, 2→4, 3→6）
- 边界：max(-20, min(20, session_affinity + delta))

affinity_bonus 计算（用于总MSD子会话评分排名）：
  affinity_bonus = 1.0 + session_affinity × 0.05
  session_affinity=-20 → bonus=0.0（权重归零，自然跳过检索）
  session_affinity=0   → bonus=1.0（中性）
  session_affinity=+20 → bonus=2.0（最高加成）

用户手动重置：
  大模型识别解除意图 → 告知用户当前值和可取范围（-20到+20）
  → 用户指定值 → 直接写入 session_affinity

### global_graph_index（总MSD，每个节点=一个子MSD即一个会话）
说明：总MSD与子MSD是两张独立的表，不是嵌套关系。
      总MSD节点 → 代表整个会话（子MSD）
      子MSD节点 → 代表具体语义片段

```
global_node_id    TEXT PK   UUID
session_id        TEXT FK   对应的会话ID（唯一，一会话一节点）
summary           TEXT      大模型对该会话的总结摘要
keywords          TEXT      大模型提取的关键词（JSON数组）
                            ⚠️ 必须从该会话各语义片段的关键词中取词，不可凭空生成
timestamps        TEXT      时间戳数组（JSON），格式见下方
created_at        INTEGER   会话创建时间
updated_at        INTEGER   会话最后活跃时间
```

timestamps 字段结构（每个元素代表一个7天连续会话段的中位时间戳）：
```json
[
  {
    "ts": 1748000000,
    "range_start_id": "fragment_id_xxx",
    "range_end_id":   "fragment_id_yyy"
  }
]
```

时间戳生成规则：
- 以7天为一个窗口，窗口内相邻片段间隔 ≤1天视为连续
- 连续满7天 → 取该窗口内所有片段 created_at 的中位值，生成一个时间戳
- 中间断开（间隔>1天）→ 重新计时
- 不足7天的尾段 → 由首尾时间戳（created_at/updated_at）覆盖，不额外生成

时间戳联动删除规则（GC触发时）：
- 某语义片段被GC删除 → 检查它是否在某时间戳的 range_start_id~range_end_id 范围内
- 是 → 从该范围剩余片段重新计算中位时间戳 → 更新总MSD该节点的对应ts值
- 范围内片段全部删完 → 删除该时间戳条目
- 通过 created_at 范围判断片段归属，range_start_id/range_end_id 作为首尾锚点

总MSD维护规则：
- 每次新建会话 → 在 global_graph_index 新增节点
- 每次会话有新片段归档 → 更新对应节点的 summary/keywords/timestamps/updated_at
- 子MSD（会话）被删除 → 从 global_graph_index 删除对应节点

### fragments_fts（FTS5全文检索虚拟表）
```sql
CREATE VIRTUAL TABLE fragments_fts USING fts5(
    fragment_id UNINDEXED,
    summary,
    keywords,
    content='memory_fragments',
    content_rowid='rowid'
);
```
- 写入 memory_fragments 时同步 INSERT 此表
- GC删除 fragment 时同步从此表 DELETE
- 零额外依赖，SQLite内置支持

---

## 四、写入流程（sync_turn钩子）

语义切分三模式加权（不可简化）：
- 话题边界权重：0.6
- 时间间隔权重：0.2
- 长度限制权重：0.2（单片段上限500字，超出强制切分）

执行顺序：
1. 按三模式加权切分对话为语义片段
2. 调用 Hermes 辅助模型生成 summary 和 keywords（通过config.py读取API配置）
3. 写入 memory_fragments（is_core默认0，weight默认1.0）
4. 同步更新 fragments_fts 全文索引
5. 调用辅助模型分析新片段，提取实体 → 写入 graph_nodes
6. 建立新节点与已有节点的关系 → 写入 graph_edges
7. 检测新事实是否与已有节点冲突：
   - 冲突：旧节点 status → superseded，valid_until = now()
   - 建立 supersedes 边
   - 扫描旧节点关联节点 → status → superseded（implies_outdated边）
8. 所有图谱变更同步写入 mutation_log

用户自定义总结双流程（触发条件：用户说「刚才这段总结是xxx」）：
- Step1：AI仅做语句通顺/标点/格式优化，不改语义
- Step2：展示优化版：「我帮你整理了一下，你看这样可以吗？」
- Step3：用户确认 → archive_type=manuel，is_manual=1，weight=2.0
- Step4：人工总结永不被AI自动更新

---

## 五、检索流程（prefetch钩子）

> 详细算法和逻辑图见技术设计文档 §五。此处为可执行摘要。

检索分五个阶段顺序执行，不可跳步：

**阶段一：预处理**

Step1  is_core=1 片段强制加入候选集（最高优先级，无论查询内容）

Step2  调用辅助模型判断用户输入的时间概念：
- 有时间概念 → 输出时间中心点 + 置信半径（天数）
  - 历史词（前/以前/曾经/去年/上次）→ superseded节点本阶段可见
  - 当前词（现在/目前/最近/今天）→ superseded节点完全屏蔽
  - 置信度 < 0.6（极度模糊）→ 先返回一句话让用户确认再继续
- 无时间概念 → 中心点=now()，半径=∞
- 确定评分模式权重：
  - 有时间概念：T_weight=0.65，K_weight=0.25
  - 无时间概念：T_weight=0.30，K_weight=0.60

**阶段二：候选集构建（两路并行，只取fragment_id，不读内容）**

Step3  路径A — 图谱检索：
- 提取用户输入中的实体词（人名/地点/职业/概念等）
- 在 graph_nodes.entity_name 中精确匹配，按status过滤
- 沿 graph_edges 遍历，按affinity降序，收集 fragment_id

Step4  路径B — FTS5关键词检索：
- 对用户输入分词，在 fragments_fts 中检索 keywords 和 summary 字段
- 取 Top-20 fragment_id

两路结果合并去重 → 候选 fragment_id 集合

**阶段三：统一评分**

Step5  对候选集每个fragment读取轻量字段（created_at/keywords/summary/weight/is_core/is_manual），
按以下公式评分：

```
Score = T_score × T_weight
      + K_score × K_weight
      + W_score × 0.10
      + C_score × 0.10

T_score（时间得分）：
  d = 片段created_at距时间中心点天数
  r = 置信半径
  d <= r：T_score = 1.0 - 0.30*(d/r)          # 范围内 1.0→0.70
  d >  r：T_score = 0.70 * exp(-0.05*(d-r))    # 范围外从0.70衰减
  无时间概念：T_score = exp(-0.05*d)            # 纯时间衰减

K_score（关键词得分）：
  effective_hits = keywords命中数 + summary命中数×0.5
  K_score = min(1.0, (2**effective_hits - 1) / (2**n - 1))
  n = 片段keywords总数，effective_hits=0时K_score=0

W_score（用户标记权重）：
  W_score = log(weight+1) / log(max_weight+1)  # 对数归一化

C_score（核心记忆加成）：
  is_core=1 → 1.0，否则 0.0
```

is_manual=1 的片段：最终 Score × 2.0

按 Score 降序排列所有候选片段。

**阶段四：加载深度决策**

```
is_core=1 的片段      → 强制加载 summary + raw_text（不占排名名额）
排名 1-5（得分最高）  → 加载 summary + raw_text
排名 6-20             → 只加载 summary
排名 20 之后          → 截断，不加载

总注入字数 ≤ 500字，超出时 raw_text 先截断，summary 完整保留
```

**阶段五：质量检查 → 跨会话触发判断**

```
满足以下任一条件 → 触发跨会话检索（见五B）：
  自动：本会话 Top1 得分 < 0.3，或命中片段数 < 2
  显式：用户输入含 @session-xxx 或"上次我们聊的"等跨会话语义
```

> 默认只检索当前会话 + 公共库（is_public=1）。
> 跨会话检索在阶段五触发后执行，见五B。

---

## 五B、跨会话检索流程

**触发条件（满足任一）：**
- 自动：本会话 Top1 得分 < 0.3，或命中片段数 < 2（静默执行，用户无感知）
- 显式：用户输入含 @session-xxx 或"上次我们聊的"、"之前讨论过"等

**路由逻辑（按优先级）：**

路径一：快速通道（最优先）
- 检查 sessions.fast_lane_sessions 字段
- 目标会话在快速通道中 → 直接检索，跳过路由

路径二：总表路由（活跃会话 ≥ 5 时）
- 在 global_graph_index 中按实体/关键词检索
- 取命中的 source_session_id，按关联权重排序
- 依次进入目标会话执行阶段二~四的检索

路径三：遍历检索（活跃会话 < 5 时）
- 按 sessions.updated_at 由近及远遍历所有会话
- 逐会话执行阶段二~四检索

**跨会话评分融合：**
```
# 跨会话片段与本会话片段在同一得分池竞争
cross_score = fragment_score * session_factor

session_factor：
  本会话/公共库 = 1.0
  跨会话目标    = session_graph_index.affinity
  快速通道中的  = min(1.0, affinity + 0.10)

所有片段合并排名，统一截断 Top-20
```

**命中后更新：**
- session_graph_index：hit_count+1，last_hit_at=now()
- affinity = min(1.0, hit_count×0.1) × exp(-0.01×距last_hit_at天数)
- 快速通道触发：3天内命中同一目标 ≥ 3次 → 追加写入 fast_lane_sessions

**注入时标记来源：**
跨会话片段的元数据标签中追加「（跨会话）」标记，
用户可从标签感知信息来源，不打断对话流程。


## 五C、系统参数表

所有可调参数集中在 `memoir/config_params.py`，禁止在其他模块硬编码这些值。

```python
# ── 检索参数 ──────────────────────────────────────────
THRESHOLD_LOW        = 0.40  # 可信度低阈值，低于此分触发跨会话检索
THRESHOLD_HIGH       = 0.70  # 可信度高阈值，高于此分直接输出
MAX_RETURN           = 20    # 检索最大返回条数
MAX_INJECT_CHARS     = 500   # 注入上下文最大字数
TOP_FULL_LOAD        = 5     # 排名前N条同时加载raw_text，其余只加summary

# ── 评分权重（当前会话片段级）────────────────────────
W_TIME_FIRST_T       = 0.40  # 有时间概念时，时间得分权重
W_TIME_FIRST_K       = 0.40  # 有时间概念时，关键词得分权重
W_KEYWORD_FIRST_T    = 0.20  # 无时间概念时，时间得分权重
W_KEYWORD_FIRST_K    = 0.60  # 无时间概念时，关键词得分权重
W_USER_MARK          = 0.05  # 用户标记权重（W_score分项）
W_PUBLIC_MEMORY      = 0.15  # 公共记忆加成（C_score分项）

# ── 评分权重（总MSD子会话级）─────────────────────────
W_MSD_TIME_FIRST_T   = 0.50  # 总MSD有时间概念时，时间权重
W_MSD_TIME_FIRST_K   = 0.50  # 总MSD有时间概念时，关键词权重
W_MSD_KEYWORD_T      = 0.20  # 总MSD无时间概念时，时间权重
W_MSD_KEYWORD_K      = 0.80  # 总MSD无时间概念时，关键词权重

# ── 跨会话参数 ────────────────────────────────────────
CROSS_SESSION_MAX_ROUNDS      = 5   # 跨会话统计路由最大循环轮次
CROSS_SESSION_TOP_PER_SESSION = 5   # 每个子会话最多返回片段条数
AFFINITY_MAX                  = 20  # session_affinity上限
AFFINITY_MIN                  = -20 # session_affinity下限
AFFINITY_STEP_THRESHOLD       = 10  # 超过此绝对值后步长翻倍

# ── GC参数 ────────────────────────────────────────────
GC_BASE_DAYS    = 60   # GC基础存活天数，实际阈值 = GC_BASE_DAYS × weight
GC_PHASE2_DAYS  = 180  # 第一阶段后多少天执行第二阶段完整删除
GC_MAX_WEIGHT   = 3.0  # weight超过此值不触发GC

# ── 图谱遍历参数 ──────────────────────────────────────
GRAPH_TRAVERSE_MAX_DEPTH      = 2    # 图谱遍历最大跳数，防止链式爆炸
GRAPH_TRAVERSE_MIN_AFFINITY   = 0.3  # 低于此affinity的边不遍历

# ── 总MSD时间戳参数 ───────────────────────────────────
MSD_TIMESTAMP_WINDOW  = 7  # 时间戳生成窗口（天），连续满N天生成一个时间戳
MSD_TIMESTAMP_GAP     = 1  # 超过N天无会话视为断开，重新计时
```

---

---

## 六、检索评分公式

> 完整推导和示例见技术设计文档 §5.5。此处为实现规范。

**统一公式（有/无时间概念切换权重比例）：**
```
Score = T_score × T_weight
      + K_score × K_weight
      + W_score × 0.10
      + C_score × 0.10

有时间概念：T_weight=0.65，K_weight=0.25
无时间概念：T_weight=0.30，K_weight=0.60
```

**T_score 实现：**
```python
if r == float('inf'):               # 无时间概念
    T_score = exp(-0.05 * d)
elif d <= r:                        # 在时间范围内
    T_score = 1.0 - 0.30 * (d / r)
else:                               # 超出时间范围
    T_score = 0.70 * exp(-0.05 * (d - r))
# d=距时间中心点天数，r=置信半径
```

**K_score 实现：**
```python
effective_hits = h_k + h_s * 0.5   # h_s为summary命中数，折算0.5
if n == 0 or effective_hits == 0:
    K_score = 0.0
else:
    K_score = min(1.0, (2**effective_hits - 1) / (2**n - 1))
# n=片段keywords总数，结果严格在[0,1]不溢出
```

**W_score 实现：**
```python
W_score = log(weight + 1) / log(max_weight + 1)
# max_weight = 当前库中所有片段weight的最大值（每次prefetch时查一次）
```

**C_score：** is_core=1 → 1.0，否则 0.0

**最终加成：** is_manual=1 → Score × 2.0

⚠️ 禁止使用乘法公式（任一项为0则总分归零）
上限：返回最多20条，注入字数 ≤ 500字

---

## 七、注入格式（末尾必须追加）

正常片段：
```
> 💡 来源: {session_name} | 时间: {date} | 标签: {keywords} | 权重: {weight}
```

过时片段（历史模式下返回的 superseded 节点）：
```
> 💡 来源: {session_name} | 时间: {date} | 标签: {keywords} | 权重: {weight}
> ⚠️ 此信息已于 {superseded_at} 过时：{superseded_reason}
```

用户追问过时内容时（如「为什么过时了」「当时说的什么」）：
- 从 graph_nodes.triggered_by_fragment_ids 取出原始 fragment_id 列表
- 加载对应 memory_fragments.raw_text，注入上下文供模型回答
- 加载这些 fragment 不计入常规 prefetch 的20条上限

跨会话引用语法：`@session-{会话ID} {关键词}`
跨通道引用语法：`@{通道名} {时间} {关键词}`

---

## 八、GC冷存储机制

### 触发条件（三个同时满足才执行第一阶段）

条件1：weight ≤ 3.0
条件2：距 created_at 已超过 60 × weight 天
条件3：距 last_accessed_at 已超过 60 × weight 天
示例：weight=1.0 → 60天，weight=2.0 → 120天，weight=3.0 → 180天

### 禁止GC的例外（以下任一成立则完全跳过）
- is_core = 1
- is_public = 1
- 对应 graph_nodes.status = superseded（历史溯源锚点，永久保留）

### 第一阶段（立即执行）

操作：
- 清空 memory_fragments.raw_text（置为NULL，保留其余所有元数据）
- 标记 memory_fragments.gc_deleted_at = now()
- 从 fragments_fts 删除对应索引
- 追加写入 archive/cold_memory.jsonl（保留完整字段含raw_text）
- graph_nodes.gc_pending = 1，gc_pending_since = now()
- ⚠️ 不删除 memory_fragments 记录，不删除 graph_nodes 节点

第一阶段后被检索命中时的处理：
- 通过 fragment_id 回表取 summary/keywords（仍在 memory_fragments 里）
- 返回：「此记忆片段（{summary}）已于 {gc_deleted_at} 删除，
         标签：{keywords}」
- 提示：「收藏或手动打分可延长存储时间」

### 第二阶段（第一阶段后180天执行）

扫描条件：gc_pending=1 且 gc_pending_since > 180天前

操作：
- 从 memory_fragments 删除整条记录
- 从 graph_nodes 删除该节点
- 从 graph_edges 删除相关边
- 检查 global_graph_index 对应会话节点的 timestamps：
    → 该 fragment 的 created_at 若在某时间戳覆盖范围内，
      重新计算该时间戳（从范围内剩余未删除片段取中位值）
    → 范围内片段全部删完 → 删除该时间戳条目
- 检查 global_graph_index 对应会话节点的 keywords：
    → 若某关键词只来源于被删节点 → 从 keywords 数组中移除
- 写回 global_graph_index

⚠️ 第二阶段独立扫描，与第一阶段完全解耦，每次 run_gc() 时同步执行两个阶段

---

## 九、自进化流程

触发：用户修改AI生成的归档总结
操作：将 {原始AI总结, 用户修改版本} 存入 evolution_samples 表
使用：下次归档时检索最近5条高权重样本作为 Few-Shot 注入归档Prompt
重置：用户指令「重置学习状态」→ 清空 evolution_samples 表

> evolution_samples 是内存表还是持久化表：持久化存入 memoir.db
> 表结构：sample_id TEXT PK | created_at INTEGER | ai_version TEXT | user_version TEXT | diff_score REAL | weight REAL

---

## 十、项目目录结构

```
~/AMSM/
├── SPEC.md                   ← AI执行手册（每次任务前必读）
├── memoir.db                 ← 运行时数据库（不提交Git）
├── memoir/
│   ├── __init__.py
│   ├── config.py             ← 运行时读取Hermes配置（辅助模型API，不缓存不硬编码）
│   ├── provider.py           ← MemoryProvider主入口（4个钩子）
│   ├── archive.py            ← 归档引擎（sync_turn调用）
│   ├── graph.py              ← 图谱引擎（节点/关系/冲突检测）
│   ├── retrieval.py          ← 检索引擎（prefetch调用）
│   ├── evolution.py          ← 自进化模块（Few-Shot样本管理）
│   ├── gc.py                 ← GC冷存储模块
│   └── db/
│       ├── __init__.py
│       ├── schema.py         ← 7张表建表语句（含FTS5虚拟表）
│       └── conn.py           ← 数据库连接管理
├── tests/
│   ├── test_smoke.py
│   ├── test_archive.py
│   ├── test_graph.py
│   └── test_retrieval.py
└── archive/
    └── cold_memory.jsonl     ← GC冷存储（追加，不删改）
```

---

## 十一、Phase执行计划

### Phase 0 — 环境核查（只读，不做任何修改）

目标：确认执行环境满足所有前置条件

执行步骤：
1. 找到Hermes实际安装路径，读取MemoryProvider接口源文件，
   记录四个钩子的实际参数签名（initialize/prefetch/sync_turn/shutdown）
2. 找到Hermes配置文件，记录辅助模型对应的字段名（API endpoint和model name）
   ⚠️ 必须实际读文件，不能假设字段名
3. 确认Python版本 ≥ 3.10（来自~/hermes-env/）
4. 确认SQLite支持FTS5：
   `~/hermes-env/bin/python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); print('FTS5 OK')"`
5. 确认~/AMSM/目录是否存在

验收标准（全部满足才能进入Phase 1）：
- □ 四个钩子实际签名已记录（实际读取，非记忆）
- □ Hermes辅助模型配置字段名已记录
- □ Python ≥ 3.10 确认
- □ SQLite FTS5 可用确认
- □ 无需安装任何额外Python包

STOP，输出以上状态报告，等待确认后进入Phase 1。

---

### Phase 1 — 目录骨架

目标：创建所有文件和目录，只有函数签名，无实现逻辑

执行步骤：
1. 创建目录结构：
   ```
   mkdir -p ~/AMSM/memoir/db
   mkdir -p ~/AMSM/tests
   mkdir -p ~/AMSM/archive
   ```

2. 创建以下文件（只含import和函数签名，函数体只写pass）：
   ```
   ~/AMSM/memoir/__init__.py
   ~/AMSM/memoir/config.py       ← load_hermes_config()签名
   ~/AMSM/memoir/provider.py     ← 四个钩子签名（按Phase 0读到的实际签名）
   ~/AMSM/memoir/archive.py      ← archive_fragment()签名
   ~/AMSM/memoir/graph.py        ← update_graph(), detect_conflict()签名
   ~/AMSM/memoir/retrieval.py    ← retrieve()签名
   ~/AMSM/memoir/evolution.py    ← record_sample(), build_prompt()签名
   ~/AMSM/memoir/gc.py           ← run_gc()签名
   ~/AMSM/memoir/db/__init__.py
   ~/AMSM/memoir/db/schema.py    ← 5张表建表SQL常量（含FTS5虚拟表）
   ~/AMSM/memoir/db/conn.py      ← get_connection()签名
   ~/AMSM/tests/test_smoke.py
   ```

3. 创建~/AMSM/SPEC.md（本文件）

验收标准：
- □ 所有文件存在（逐一验证路径，H-8）
- □ `python3 -m py_compile` 每个.py文件无报错
- □ provider.py的钩子签名与Phase 0读到的实际签名一致
- □ config.py中有load_hermes_config()，不含任何硬编码值

STOP，输出文件列表和语法检查结果，等待确认。

---

### Phase 2 — 数据库初始化

目标：memoir.db创建成功，5张表结构完全正确，FTS5可用

执行步骤：
1. 在db/schema.py中写入5张表的完整建表SQL（含fragments_fts虚拟表）
2. 在db/conn.py中实现get_connection()：
   - 使用~/hermes-env/中的Python
   - 打开memoir.db
   - 执行 `PRAGMA journal_mode=WAL`
   - 执行 `PRAGMA foreign_keys=ON`
   - ⚠️ 不加载sqlite-vec，不需要
3. 创建init_db.py脚本执行建表

TDD验证（必须通过才能继续）：
- 写入1条测试session和1条测试fragment
- 断言：50ms内通过session_id查出，所有字段存在且类型正确
- 断言：fragments_fts中能通过关键词检索到该fragment
- 运行：`~/hermes-env/bin/python tests/test_smoke.py`
- 期望输出包含：PASS

STOP，输出测试结果，等待确认。

---

### Phase 3 — MemoryProvider骨架

目标：Hermes能加载AMSM插件，启动不报错，4个钩子被调用

执行步骤：
1. 实现config.py中的load_hermes_config()：
   - 读取Hermes配置文件（Phase 0确认的路径）
   - 提取辅助模型的API endpoint和model name
   - 返回dict，不做任何缓存，每次initialize时重新读取
2. 在provider.py实现四个钩子的最小可运行版本：
   - initialize：调用load_hermes_config()，打开DB连接，建表（如不存在）
   - prefetch：直接返回空字符串""
   - sync_turn：直接pass
   - shutdown：关闭DB连接
3. 按Hermes文档注册插件（参考Phase 0读到的接口）
4. 在Hermes config中激活AMSM

TDD验证：
- 启动Hermes，发送一条测试消息「你好」
- 检查输出不包含：ImportError / MemoryProvider报错 / AMSM相关错误

STOP，输出Hermes启动日志，等待确认。

---

### Phase 4A — 归档引擎

目标：sync_turn结束后memory_fragments有新记录，FTS5索引同步更新

执行步骤：
实现archive.py中的archive_fragment()：
- 接收对话文本
- 按三模式加权切分（话题0.6+时间0.2+长度0.2）
- 通过config.py取到的辅助模型API，生成summary和keywords
- 写入memory_fragments（含所有必要字段）
- 同步写入fragments_fts（INSERT INTO fragments_fts(fragment_id, summary, keywords)）
- 返回fragment_id

TDD验证（tests/test_archive.py）：
- Mock一段5轮对话，调用archive_fragment()
- 断言：DB中记录数+1
- 断言：新记录包含fragment_id/session_id/created_at/summary/keywords/raw_text
- 断言：fragments_fts中能通过keywords检索到新记录
- 期望输出：test_archive PASS

STOP，输出测试结果，等待确认。

---

### Phase 4B — 图谱引擎

目标：graph_nodes和graph_edges正确写入，冲突检测正确触发

执行步骤：
实现graph.py中的update_graph()和detect_conflict()：
- 通过config.py取到的辅助模型API分析fragment，提取实体，写入graph_nodes
- 建立节点间关系，写入graph_edges（使用合法relation_type）
- 检测新节点与已有节点是否冲突
- 冲突时：旧节点status→superseded，valid_until=now()
- 写入mutation_log（只INSERT，绝不UPDATE/DELETE，H-4）

TDD验证（tests/test_graph.py）：
- Step1：写入fragment「用户是结构工程师」→ 建立graph_node
- Step2：写入fragment「用户转行做电商」→ 触发冲突检测
- 断言：旧节点status=superseded，valid_until不为NULL
- 断言：mutation_log行数+1，reason字段非空
- 断言：新节点status=active
- 期望输出：test_graph PASS

STOP，输出测试结果，等待确认。

---

### Phase 4C — 检索引擎

目标：完整检索流程执行正确，评分公式正确，加载深度决策正确

执行步骤：
实现retrieval.py中的retrieve()，严格按§五的五阶段执行：
- 阶段一：调用辅助模型判断时间概念，确定T_weight/K_weight，处理superseded路由
- 阶段二：图谱检索（路径A）+ FTS5检索（路径B）并行，合并去重取fragment_id集合
          ⚠️ 此阶段只取ID，不读raw_text
- 阶段三：对候选集按§六评分公式评分，降序排列
- 阶段四：is_core强制加载raw_text；排名1-5加载raw_text；排名6-20只加summary
- 阶段五：Top1得分<0.3或命中<2 → 触发跨会话检索（见retrieve_cross_session()）

TDD验证（tests/test_retrieval.py）：

测试A（有时间概念，历史模式）：输入「我以前的同事怎么样了」
  - 断言：T_weight=0.65，K_weight=0.25
  - 断言：命中superseded节点（Phase 4B创建的旧工作节点）
  - 断言：返回文本末尾包含「⚠️」

测试B（无时间概念，当前模式）：输入「我现在的工作怎样」
  - 断言：T_weight=0.30，K_weight=0.60
  - 断言：只返回active节点，不包含superseded节点

测试C（加载深度）：准备10条片段，得分各不同
  - 断言：排名1-5的片段raw_text已加载（非空）
  - 断言：排名6-10的片段raw_text为空，summary非空

测试D（跨会话触发）：清空当前会话片段，输入「电商」
  - 断言：本会话Top1得分<0.3，触发跨会话检索
  - 断言：retrieve_cross_session()被调用，无异常

期望输出：test_retrieval PASS（4/4）

STOP，输出测试结果，等待确认。

---

### Phase 5 — 核心记忆与自进化

目标：is_core优先加载正常，Few-Shot样本采集和注入正常

执行步骤：
1. 实现is_core标记逻辑：
   - 用户收藏触发：is_core=1，core_reason=user_bookmarked
   - AI判断长期事实：is_core=1，core_reason=ai_classified
2. 验证prefetch的Step1优先加载is_core=1片段
3. 实现evolution.py：
   - record_sample()：记录AI原版vs用户修改版到evolution_samples
   - build_prompt()：检索最近5条高权重样本，构建Few-Shot Prompt

TDD验证：
- 写入1条is_core=1的片段
- 执行prefetch，断言：返回列表第一条是该is_core=1片段
- 写入1条evolution_sample
- 执行build_prompt()，断言：返回的prompt字符串包含该样本内容
- 期望输出：test_core_memory PASS

STOP，输出测试结果，等待确认。

---

### Phase 6 — GC冷存储

目标：两阶段GC正确执行，触发条件按weight动态计算，protected片段不被GC

执行步骤：
实现gc.py中的run_gc()，严格按§八的两阶段逻辑：

第一阶段扫描条件（三个同时满足才执行）：
- 条件1：weight ≤ 3.0
- 条件2：距 created_at 超过 60 × weight 天
- 条件3：距 last_accessed_at 超过 60 × weight 天
- 例外跳过：is_core=1 / is_public=1 / 对应graph_node.status=superseded

第一阶段操作：
- 从 memory_fragments 删除
- 从 fragments_fts 删除对应索引
- 追加写入 archive/cold_memory.jsonl（保留完整字段）
- 对应 graph_nodes 的 gc_pending 置1，gc_pending_since = now()
- ⚠️ 不删除 graph_nodes，图谱结构保留

第二阶段（独立扫描，每次run_gc时同步执行）：
- 扫描 gc_pending=1 且 gc_pending_since > 180天前 的节点
- 从 graph_nodes 删除
- 从 graph_edges 删除相关边
- 从 global_graph_index 删除对应条目

TDD验证（tests/test_gc.py）：

测试A（应被GC第一阶段）：weight=1.0，created_at=70天前，last_accessed_at=70天前
  - 阈值=60×1.0=60天，两个时间都超过60天
  - 断言：memory_fragments.raw_text=NULL，gc_deleted_at非NULL
  - 断言：fragments_fts同步删除，cold_memory.jsonl+1行（含完整raw_text）
  - 断言：memory_fragments其余字段（summary/keywords）仍存在
  - 断言：graph_node.gc_pending=1

测试B（weight保护）：weight=2.0，created_at=100天前，last_accessed_at=100天前
  - 阈值=60×2.0=120天，100天未超过
  - 断言：raw_text未被清空，gc_deleted_at=NULL

测试C（is_core保护）：weight=1.0，created_at=70天前，is_core=1
  - 断言：raw_text未被清空

测试D（superseded保护）：weight=1.0，created_at=70天前
  对应graph_node.status=superseded
  - 断言：raw_text未被清空（历史溯源锚点不可GC）

测试E（第一阶段后检索返回）：对测试A的片段执行检索
  - 断言：返回文本包含summary和keywords
  - 断言：返回文本包含gc_deleted_at时间
  - 断言：不抛异常

测试F（第二阶段）：手动写入gc_pending=1且gc_pending_since=181天前的node
  - 执行run_gc()
  - 断言：memory_fragments整条记录删除
  - 断言：graph_nodes节点删除，相关edges删除
  - 断言：global_graph_index对应会话节点timestamps更新

期望输出：test_gc PASS（6/6）

STOP，输出测试结果，等待确认。

---

### Phase 7 — 端到端集成测试

目标：完整流程跑通，数据一致，无H系列违反

T1：正常对话→归档→图谱写入→FTS5索引同步
T2：事实更新→图谱冲突检测→mutation_log记录
T3：历史模式检索→T_weight=0.65→命中superseded节点→⚠️标签出现
T4：当前模式检索→T_weight=0.30→只返回active节点
T5：核心记忆优先加载→is_core=1片段强制加载raw_text→排在注入内容最前
T6：加载深度验证→排名1-5有raw_text，排名6-20无raw_text
T7：跨会话自动触发→本会话命中<2→retrieve_cross_session被调用→注入标签含「跨会话」

所有T测试通过后，检查无H系列约束违反（无raw_text修改，无mutation_log删除）

期望输出：Integration Test PASS（5/5）

STOP，输出完整测试报告，等待最终确认。

---

## 十二、当前执行状态

已完成：Phase 0-5 全部 ✅
当前：Phase 4D（分模块开发中）

Phase 4D 采用分模块开发方式：
各子Agent新开对话，读总SPEC后执行指定模块，
开发完成交付文件，最终由主Agent合并后统一跑TDD验收。

子模块进度：
  M4 scorer.py        ⬜ 待开发
  M5 retrieval.py     ⬜ 待开发（依赖M4）
  M6 cross_session.py ⬜ 待开发（依赖M4、M5）

主项目下一步：
  等M4/M5/M6全部交付后，合并文件，运行完整TDD验收

---

## 十三、版本变更记录

| 版本 | 日期 | 核心变更 |
|------|------|----------|
| V1.0-V3.1 | 2026-05 | 历史版本，见完整需求文档 |
| V4.0 | 2026-05-20 | 完整合并版：向量检索+BM25+Ollama方案 |
| V5.0 | 2026-05-20 | 轻量化重构：移除sqlite-vec/rank-bm25/Ollama依赖；用SQLite FTS5替代向量+BM25兜底；config.py动态读取Hermes辅助模型配置；数据库从6张表简化为5张表 |
| V6.0 | 2026-05-20 | 关键词多命中指数加成；weight用于AI学习/检索对数归一化分离；过时标记含原因和触发消息；跨会话关联权重+快速通道；总表记忆结构图（≥5会话自动创建）；GC两阶段执行+基础时间×weight存活公式；数据库扩展至7张表 |
| V6.1 | 2026-05-21 | 检索流程重构：五阶段统一评分；时间模糊区间（中心点+置信半径）；加载深度由评分决定（前5加raw_text/6-20只加summary）；跨会话两个触发条件（得分<0.3自动/用户显式）；跨会话评分融合进同一得分池；Phase 4C TDD更新4个测试用例；Phase 7集成测试扩展至T7 |
