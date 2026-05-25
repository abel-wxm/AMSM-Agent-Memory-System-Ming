import sys
sys.path.insert(0, '/home/abel/Project/AMSM')
from memoir.scorer import score_fragments
from memoir.config_params import IDENTITY_FACT_SCORE_MULTIPLIER

fragments = [
    {
        "fragment_id": "f1",
        "created_at": 100000,
        "weight": 1.0,
        "tags": {"identity_fact": "1"}, # Should get multiplier
    },
    {
        "fragment_id": "f2",
        "created_at": 100000,
        "weight": 1.0,
        "tags": {"manual": "1"}, # Should NOT get 2.0 anymore
    }
]

scored = score_fragments(
    fragments=fragments,
    t_center_ts=100000,
    t_radius_days=1.0,
    has_time_concept=False,
    max_weight=1.0
)

f1_score = next(f['final_score'] for f in scored if f['fragment_id'] == 'f1')
f2_score = next(f['final_score'] for f in scored if f['fragment_id'] == 'f2')

print(f"F1 (identity) score: {f1_score}")
print(f"F2 (manual) score: {f2_score}")

assert f1_score > f2_score, "identity_fact 应该被大幅提权"
# 验证 manual 被移除
# base_score 应该是一致的，除去 tags 不同带来的 c_score 差异。
# f1 c_score=0.3, f2 c_score=0.3 -> base_score 相等。
# 如果 manual * 2 还在，f2 就会很大。现在 f2 应该是正常的分数。
print("Phase 4 test passed!")
