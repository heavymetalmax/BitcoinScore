"""Metric mapping primitives — Layer 1 boundary.

All map_*() functions, ADAPTIVE_* constants, _load_metric_history, and
_oc_coherence live here so that normalizer.py can import them WITHOUT
depending on scoring.py (V1) or any higher-layer scorer.

Rule: this file MUST NOT import from scoring.py, score.py, scoring_v2.py,
or any file that in turn imports those scorers.
"""
import math
import os
import json
import datetime

# ── Adaptive normalization constants ────────────────────────────────────────
# Valuation metrics whose cyclical extremes compress as Bitcoin matures get a
# blended risk score: w * (rolling-percentile) + (1-w) * (fixed map).
ADAPTIVE_METRICS = {'nupl', 'mvrv', 'mayer', 'cvdd_ratio', 'puell', 'rhodl_ratio', 'etf_flows', 'dxy'}
_UNIFIED_FIELD   = {'mayer': 'mayer_multiple', 'cvdd_ratio': 'cvdd_ratio'}
_DV_KEY          = {'mayer': 'mayer_multiple', 'cvdd_ratio': 'cvdd_ratio'}
ADAPTIVE_BLEND         = 0.7    # 70% percentile + 30% fixed map
ADAPTIVE_WIN_YEARS     = 4      # trailing window for percentile
ADAPTIVE_BLEND_OVERRIDE = {
    'nupl':        1.0,
    'mvrv':        1.0,
    'cvdd_ratio':  1.0,
    'rhodl_ratio': 1.0,
    'puell':       1.0,
}
ADAPTIVE_DEBUG: dict = {}
_HIST_CACHE: dict    = {}
_SCORES_CACHE        = None


def _load_scores_cache():
    global _SCORES_CACHE
    if _SCORES_CACHE is not None:
        return _SCORES_CACHE
    path = 'data/history/scores.json'
    if os.path.exists(path):
        _SCORES_CACHE = json.load(open(path, encoding='utf-8'))
    else:
        _SCORES_CACHE = []
    return _SCORES_CACHE


def _load_metric_history(metric):
    """Return [(date_str, value)] from scores.json (the ONE database)."""
    if metric in _HIST_CACHE:
        return _HIST_CACHE[metric]

    field = _UNIFIED_FIELD.get(metric, metric)
    pts: list = []

    for row in _load_scores_cache():
        v = row.get(field)
        d = row.get('date', '')[:10]
        if d and v is not None:
            pts.append((d, float(v)))

    # etf_flows lives in a separate flat-list file
    if not pts and metric == 'etf_flows':
        etf_path = 'data/history/etf_flows.json'
        try:
            if os.path.exists(etf_path):
                raw_etf = json.load(open(etf_path, encoding='utf-8'))
                if isinstance(raw_etf, list):
                    for r in raw_etf:
                        d = (r.get('timestamp') or r.get('date', ''))[:10]
                        v = r.get('etf_flow_7d') or r.get('etf_flow_14d')
                        if d and v is not None:
                            pts.append((d, float(v)))
        except Exception:
            pass

    # Tail: daily_vector for very recent dates not yet flushed to scores.json
    try:
        dv = 'data/history/daily_vector.json'
        if os.path.exists(dv):
            dv_key = _DV_KEY.get(metric, metric)
            for row in json.load(open(dv, encoding='utf-8')):
                v = (row.get('raw') or {}).get(dv_key)
                if v is not None:
                    pts.append((row['date'], float(v)))
    except Exception:
        pass

    pts.sort(key=lambda r: r[0])
    _HIST_CACHE[metric] = pts
    return pts


# ── Metric-to-score mapping functions ───────────────────────────────────────

def map_nupl(v):
    if v is None: return None
    v = max(-50.0, min(100.0, v))
    if v <= 40.0:
        score = 8 + ((v - (-20.0)) / (40.0 - (-20.0))) * (50 - 8)
    else:
        score = 50 + ((v - 40.0) / (75.0 - 40.0)) * (100 - 50)
    return round(max(0, min(100, score)))


def map_mvrv(v):
    if v is None: return None
    v = max(-0.5, min(5.0, v))
    if v <= 1.0:
        score = 8 + ((v - (-0.3)) / (1.0 - (-0.3))) * (50 - 8)
    else:
        score = 50 + ((v - 1.0) / (5.0 - 1.0)) * (100 - 50)
    return round(max(0, min(100, score)))


def map_asopr(v):
    if v is None: return None
    MIN, BASE, MAX = 0.88, 1.00, 1.12
    if v <= BASE:
        score = ((v - MIN) / (BASE - MIN)) * 50
    else:
        score = 50 + ((v - BASE) / (MAX - BASE)) * 50
    return round(max(0, min(100, score)))


def map_fear_greed(v):
    if v is None: return None
    if isinstance(v, dict):
        val = (v.get('avg_7d') if v.get('avg_7d') is not None
               else v.get('latest') if v.get('latest') is not None
               else v.get('value'))
        if val is None: return None
        return round(max(0, min(100, val)))
    return round(max(0, min(100, v)))


def map_m2(v):
    """Global M2 YoY %: high expansion → low risk."""
    if v is None: return None
    v = max(-2, min(15, v))
    return round(((15 - v) / 17) * 100)


def map_yield_curve(v):
    """US 10Y-2Y spread: deep inversion = 100 risk, steep curve = 0."""
    if v is None: return None
    v = max(-1.0, min(2.0, v))
    return round(((2.0 - v) / 3.0) * 100)


def map_dxy(v):
    """Nominal Broad Dollar Index: high = risk-off = high score."""
    if v is None: return None
    v = float(v)
    if v <= 108.0: return 0
    if v >= 128.0: return 100
    if v <= 116.0:
        return round((v - 108.0) / (116.0 - 108.0) * 50)
    return round(50 + (v - 116.0) / (128.0 - 116.0) * 50)


def map_lth_supply(v):
    """LTH Supply % of total: high % = accumulation = low risk."""
    if v is None: return None
    v = float(v)
    if v > 100:  # raw BTC count — convert to % of hard cap
        v = v / 21_000_000 * 100
    v = max(55.0, min(82.0, v))
    return round(((82.0 - v) / (82.0 - 55.0)) * 100)


def map_mayer_multiple(v):
    """MM <= 0.5 → 0, MM >= 2.1 → 100."""
    if v is None: return None
    if isinstance(v, dict):
        return v.get('score')
    v = float(v)
    v = max(0.5, min(2.1, v))
    return round((v - 0.5) / 1.6 * 100)


def map_funding(v):
    """avg_7d range -0.05% .. +0.10%."""
    if v is None: return None
    if isinstance(v, dict):
        avg = v.get('avg_7d')
        if avg is None: return v.get('score')
        v = avg
    v = float(v)
    return round(max(0, min(100, (v + 0.05) / 0.15 * 100)))


def map_cvdd(v):
    if v is None: return None
    v = max(1, min(5, v))
    return round(math.log10(v) / math.log10(5) * 100)


def map_rhodl(v):
    if v is None: return None
    v = max(200, min(10000, v))
    score = (math.log10(v) - math.log10(200)) / (math.log10(10000) - math.log10(200)) * 100
    return round(max(0, min(100, score)))


def map_etf_flow(v):
    """7d rolling sum: <= -1000 → 0, = 375 → 50, >= 2000 → 100."""
    if v is None: return None
    if isinstance(v, dict):
        v = v.get('value')
    if v is None: return None
    v = float(v)
    if v <= -1000.0: return 0
    if v >= 2000.0:  return 100
    if v < 375.0:
        return round(((v + 1000.0) / 1375.0) * 50.0)
    return round(50.0 + ((v - 375.0) / 1625.0) * 50.0)


# ── OC coherence ─────────────────────────────────────────────────────────────
# Shared by normalizer.py and score.py.

OC_WEIGHTS = {
    'rhodl_ratio':  0.20,
    'mvrv_z_score': 0.20,
    'cvdd_ratio':   0.15,
    'nupl':         0.30,
    'asopr':        0.15,
}

_COV_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'mahalanobis_covariance.json')
_covariance_cache = None


def _load_covariance():
    global _covariance_cache
    if _covariance_cache is not None:
        return _covariance_cache
    if os.path.exists(_COV_PATH):
        try:
            with open(_COV_PATH, encoding='utf-8') as f:
                _covariance_cache = json.load(f)
        except Exception:
            _covariance_cache = {}
    else:
        _covariance_cache = {}
    return _covariance_cache


def _oc_coherence(sm: dict) -> float:
    """Mahalanobis-based phase synchrony of OC metrics → [0, 1].
    1 = all in agreement; 0 = maximally dispersed.
    """
    cov_data = _load_covariance()
    if not cov_data or 'covariance' not in cov_data or 'keys' not in cov_data:
        keys = [k for k in OC_WEIGHTS if sm.get(k) is not None]
        if len(keys) < 3:
            return 1.0
        weights = [OC_WEIGHTS[k] for k in keys]
        vals    = [sm[k] / 100.0 for k in keys]
        total_w = sum(weights)
        mean_v  = sum(w * v for w, v in zip(weights, vals)) / total_w
        var     = sum(w * (v - mean_v) ** 2 for w, v in zip(weights, vals)) / total_w
        return max(0.0, 1.0 - var ** 0.5 / 0.289)

    oc_keys = cov_data['keys']
    K = [k for k in oc_keys if sm.get(k) is not None]
    n = len(K)
    if n < 3:
        return 1.0

    x_K = [sm[k] / 100.0 for k in K]
    indices = [oc_keys.index(k) for k in K]
    Sigma_K = [[cov_data['covariance'][r][c] for c in indices] for r in indices]

    # Orthonormal basis orthogonal to [1,...,1]^T
    u1 = [1.0 / math.sqrt(n)] * n
    basis = []
    for i in range(n - 1):
        v = [0.0] * n
        v[i] = 1.0
        basis.append(v)
    all_vectors = [u1]
    for v in basis:
        for u in all_vectors:
            dot = sum(a * b for a, b in zip(v, u))
            v = [a - dot * b for a, b in zip(v, u)]
        norm = math.sqrt(sum(a * a for a in v))
        if norm > 1e-9:
            v = [a / norm for a in v]
            all_vectors.append(v)
    V_K = [[all_vectors[c][r] for c in range(1, n)] for r in range(n)]

    y = [sum(V_K[r][col] * x_K[r] for r in range(n)) for col in range(n - 1)]

    temp = [[sum(Sigma_K[r][i] * V_K[i][c] for i in range(n)) for c in range(n - 1)] for r in range(n)]
    Sigma_y = [[sum(V_K[i][r] * temp[i][c] for i in range(n)) for c in range(n - 1)] for r in range(n - 1)]

    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n - 1)] for i, row in enumerate(Sigma_y)]
    for i in range(n - 1):
        piv = max(range(i, n - 1), key=lambda r: abs(aug[r][i]))
        if abs(aug[piv][i]) < 1e-9:
            return 1.0
        aug[i], aug[piv] = aug[piv], aug[i]
        f = aug[i][i]
        aug[i] = [val / f for val in aug[i]]
        for r in range(n - 1):
            if r != i:
                fac = aug[r][i]
                aug[r] = [a - fac * b for a, b in zip(aug[r], aug[i])]
    inv_Sigma_y = [row[n - 1:] for row in aug]

    tmp_y = [sum(inv_Sigma_y[r][c] * y[c] for c in range(n - 1)) for r in range(n - 1)]
    dm_sq = sum(y[i] * tmp_y[i] for i in range(n - 1))
    dm = math.sqrt(max(0.0, dm_sq))
    return max(0.0, min(1.0, 1.0 - (dm - 1.0) / 2.5))
