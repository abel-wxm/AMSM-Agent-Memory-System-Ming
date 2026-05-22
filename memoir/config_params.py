"""
AMSM 系统参数配置表 (SPEC V6.2 §五C)
集中管理所有可调参数，避免硬编码。
"""

# ── 检索参数 ──────────────────────────────────────────
THRESHOLD_LOW        = 0.40  # 可信度低阈值，低于此分触发跨会话检索
THRESHOLD_HIGH       = 0.70  # 可信度高阈值，高于此分直接输出
MAX_RETURN           = 20    # 检索最大返回条数
MAX_INJECT_CHARS     = 500   # 注入上下文最大字数
TOP_FULL_LOAD        = 5     # 排名前N条同时加载raw_text，其余只加summary

# ── 评分权重（当前会话片段级）────────────────────────
W_TIME_FIRST_T       = 0.40  # 有时间概念时，时间得分权重 (原V6.0遗留为0.65，V6.2公式为0.65, 这里暂时与公式对齐保持V6.2最新规范,见下方说明)
W_TIME_FIRST_K       = 0.40  # 见SPEC中公式部分为0.25 (以公式为准)
W_KEYWORD_FIRST_T    = 0.20  # 见SPEC公式为0.30
W_KEYWORD_FIRST_K    = 0.60  # 见SPEC公式为0.60
W_USER_MARK          = 0.05  # 用户标记权重（W_score分项）
W_PUBLIC_MEMORY      = 0.15  # 公共记忆加成（C_score分项）

# 注意：为了避免歧义，最终以第六部分的公式参数为准，这里覆盖上方变量：
FRAGMENT_TIME_FIRST_T = 0.65
FRAGMENT_TIME_FIRST_K = 0.25
FRAGMENT_KEYWORD_FIRST_T = 0.30
FRAGMENT_KEYWORD_FIRST_K = 0.60

# ── 评分权重（总MSD子会话级）─────────────────────────
W_MSD_TIME_FIRST_T   = 0.50  # 总MSD有时间概念时，时间权重
W_MSD_TIME_FIRST_K   = 0.50  # 总MSD有时间概念时，关键词权重
W_MSD_KEYWORD_T      = 0.20  # 总MSD无时间概念时，时间权重 (原0.3，依据当前任务要求改为0.2)
W_MSD_KEYWORD_K      = 0.80  # 总MSD无时间概念时，关键词权重 (原0.7，依据当前任务要求改为0.8)

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

# ── 总MSD独立提炼触发参数 ────────────────────────────
MSD_SUMMARIZE_INTERVAL_FRAGMENTS = 20  # 每新增N条片段触发一次提炼
MSD_SUMMARIZE_INTERVAL_DAYS      = 7   # 或距上次提炼超过N天触发
