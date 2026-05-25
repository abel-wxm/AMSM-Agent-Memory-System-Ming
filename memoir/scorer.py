import math
import json
from typing import List, Dict, Any, Optional

from memoir.config_params import (
    FRAGMENT_TIME_FIRST_T,
    FRAGMENT_TIME_FIRST_K,
    FRAGMENT_KEYWORD_FIRST_T,
    FRAGMENT_KEYWORD_FIRST_K,
    W_MSD_TIME_FIRST_T,
    W_MSD_TIME_FIRST_K,
    W_MSD_KEYWORD_T,
    W_MSD_KEYWORD_K,
    W_PUBLIC_MEMORY,
    W_USER_MARK,
    IDENTITY_FACT_SCORE_MULTIPLIER,
)

def _calc_t_score(d_days: float, r_days: float) -> float:
    """
    d_days: distance to time center in days
    r_days: confidence radius in days
    """
    if math.isinf(r_days):
        return math.exp(-0.05 * d_days)
    elif d_days <= r_days:
        if r_days == 0:
            return 1.0
        return 1.0 - 0.30 * (d_days / r_days)
    else:
        return 0.70 * math.exp(-0.05 * (d_days - r_days))

def _calc_k_score(h_k: int, h_s: float, n: int) -> float:
    """
    h_k: number of keyword hits
    h_s: number of summary hits
    n: total number of keywords in the fragment
    """
    effective_hits = h_k + h_s * 0.5
    if n == 0 or effective_hits == 0:
        return 0.0
    return min(1.0, (2 ** effective_hits - 1) / (2 ** n - 1))

def _calc_w_score(weight: float, max_weight: float) -> float:
    """
    weight: weight of the fragment
    max_weight: max weight among all fragments
    """
    max_weight_clamped = max(max_weight, 1.0)
    if weight < 0:
        weight = 0.0
    return math.log(weight + 1) / math.log(max_weight_clamped + 1)

def _calc_c_score(tags: Dict[str, str]) -> float:
    """
    根据信誉度评估计算 C_score
    """
    if tags.get("favorite") == "1":
        return 1.0
    if tags.get("archive_type") == "user_specified":
        return 0.6
    return 0.3

def _count_hits(text: str, query_terms: List[str]) -> int:
    if not text or not query_terms:
        return 0
    text_lower = text.lower()
    return sum(1 for term in query_terms if term.lower() in text_lower)

def score_fragments(
    fragments: List[Dict[str, Any]],
    t_center_ts: float,
    t_radius_days: float,
    has_time_concept: bool,
    max_weight: float,
    query_terms: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Score and sort a list of fragments according to the unified formula.
    """
    if has_time_concept:
        t_weight = FRAGMENT_TIME_FIRST_T
        k_weight = FRAGMENT_TIME_FIRST_K
    else:
        t_weight = FRAGMENT_KEYWORD_FIRST_T
        k_weight = FRAGMENT_KEYWORD_FIRST_K

    for frag in fragments:
        # Time Score
        created_at = frag.get('created_at', 0)
        d_days = abs(t_center_ts - created_at) / 86400.0
        t_score = _calc_t_score(d_days, t_radius_days)

        # Keyword Score
        h_k = frag.get('h_k', 0)
        h_s = frag.get('h_s', 0)
        n = frag.get('n_keywords', 0)

        if query_terms:
            summary = frag.get('summary', '')
            keywords_raw = frag.get('keywords', '[]')
            try:
                keywords = json.loads(keywords_raw) if isinstance(keywords_raw, str) else keywords_raw
            except Exception:
                keywords = []
            
            if 'n_keywords' not in frag:
                n = len(keywords)
            
            if 'h_k' not in frag:
                h_k = 0
                for kw in keywords:
                    if any(term.lower() in str(kw).lower() for term in query_terms):
                        h_k += 1
            
            if 'h_s' not in frag:
                h_s = _count_hits(summary, query_terms)

        k_score = _calc_k_score(h_k, h_s, n)

        # Weight Score
        weight = frag.get('weight', 1.0)
        w_score = _calc_w_score(weight, max_weight)

        # Credibility Score
        tags = frag.get('tags', {})
        c_score = _calc_c_score(tags)

        # Combined Base Score
        base_score = (t_score * t_weight) + (k_score * k_weight) + (w_score * W_USER_MARK) + (c_score * W_PUBLIC_MEMORY)

        # 信用乘数提权 (Identity Fact Boost)
        if tags.get("identity_fact") == "1":
            final_score = base_score * IDENTITY_FACT_SCORE_MULTIPLIER
        else:
            final_score = base_score

        # Cross Session Factor
        session_factor = frag.get('session_factor', 1.0)
        cross_score = final_score * session_factor

        frag['t_score'] = t_score
        frag['k_score'] = k_score
        frag['w_score'] = w_score
        frag['c_score'] = c_score
        frag['base_score'] = base_score
        frag['final_score'] = final_score
        frag['score'] = cross_score

    # Sort descending by score
    fragments.sort(key=lambda x: x.get('score', 0), reverse=True)
    return fragments

def score_msd_nodes(
    nodes: List[Dict[str, Any]], 
    time_info: Dict[str, Any],
    query_terms: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Score MSD nodes (global graph index) for cross-session routing.
    """
    t_center_ts = time_info.get("time_center", 0)
    t_radius_days = time_info.get("confidence_radius", float("inf"))
    has_time_concept = time_info.get("has_time_concept", False)
    
    if has_time_concept:
        t_weight = W_MSD_TIME_FIRST_T
        k_weight = W_MSD_TIME_FIRST_K
    else:
        t_weight = W_MSD_KEYWORD_T
        k_weight = W_MSD_KEYWORD_K
        
    for node in nodes:
        session_affinity = node.get('session_affinity', 0)
        affinity_bonus = 1.0 + session_affinity * 0.05
        affinity_bonus = max(0.0, min(2.0, affinity_bonus))
        
        base_score = node.get('base_score', 1.0)
        if query_terms:
            hits = 0
            summary = node.get('summary', '')
            keywords_raw = node.get('keywords', '[]')
            try:
                keywords = json.loads(keywords_raw) if isinstance(keywords_raw, str) else keywords_raw
            except Exception:
                keywords = []
            
            keywords_str = ' '.join(str(kw) for kw in keywords).lower()
            hits += sum(1 for term in query_terms if term.lower() in keywords_str)
            hits += _count_hits(summary, query_terms) * 0.5
            
            if hits > 0:
                base_score = hits

        # Calculate time score for MSD node if time info is available
        created_at = node.get('created_at', 0)
        d_days = abs(t_center_ts - created_at) / 86400.0 if t_center_ts else 0
        t_score = _calc_t_score(d_days, t_radius_days)
        k_score = min(1.0, base_score) # basic normalization for hits
        
        final_base_score = t_score * t_weight + k_score * k_weight
        
        node['affinity_bonus'] = affinity_bonus
        node['score'] = final_base_score * affinity_bonus

    nodes.sort(key=lambda x: x.get('score', 0), reverse=True)
    return nodes
