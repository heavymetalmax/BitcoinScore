"""
Decision matrix scoring — shared between report.py and scraper.py.

On-chain group  (5 metrics, weights sum to 1.0):
  nupl ×30  rhodl_ratio ×20  mvrv_z_score ×20  cvdd_ratio ×15  asopr ×15

Tech/Macro group  (6 metrics, weights sum to 1.0):
  cipherb ×40  mayer_multiple ×20  fear_greed ×10  etf_flows ×10  yield_curve_spread ×10  m2_yoy ×10
  (cipherb — price/momentum осцилятор; +12 penalty при активній bearish divergence;
    yield_curve_spread = US 10Y-2Y Yield Curve (T10Y2Y, FRED), обернена логіка: ↓ спред (інверсія) = ↑ ризик;
    m2_yoy = Global M2 YoY % change (MacroMicro chart 3439), обернена логіка: ↑ ліквідність = ↓ ризик;

Index 1 (onchain_score) = 80% OC + 20% Tech
Index 2 (tech_score)    = 20% OC + 80% Tech
Final score             = 50% OC + 50% Tech
"""

import math
import os
import json
import datetime

# ── Adaptive normalization ("v2 calibration") ───────────────────────────────
# Valuation metrics whose cyclical extremes COMPRESS as Bitcoin matures get a
# blended risk score:  w·(rolling-percentile) + (1-w)·(fixed map).
# Only linear-mapped valuation metrics that age fastest are included; log/floor
# metrics (rhodl) and oscillators (asopr, fear_greed, funding) keep their
# fixed maps — applying a percentile to a stable-envelope oscillator would
# inject noise. Evidence: tools/adaptive_norm_probe.py (NUPL peaks 0.87→0.64,
# MVRV Z 11→3.4; fixed under-reads modern tops and over-reads modern bottoms).
ADAPTIVE_METRICS   = {'nupl', 'mvrv', 'mayer', 'cvdd_ratio', 'puell', 'etf_flows'}
# daily_vector raw key differs from the adaptive metric name for some metrics
_DV_KEY            = {'mayer': 'mayer_multiple', 'cvdd_ratio': 'cvdd_ratio'}
# field name in unified_history.json when it differs from the metric key
_UNIFIED_FIELD     = {'mayer': 'mayer_multiple', 'cvdd_ratio': 'cvdd_ratio'}
# seed history files that use dict rows: maps metric → value key inside the dict
_SEED_VAL          = {'cvdd_ratio': 'ratio'}
# unified_history/seed files store NUPL as fraction (0–1); data.json uses % (0–100).
# Divide incoming value by this factor before percentile comparison so units match.
# etf_flows history migrated 2026-07-08: etf_flow_7d field added (pre-switch entries
# use 14d/2 approximation). Live value is 7d sum; history now stores etf_flow_7d → no divisor needed.
_PCTILE_DIVISOR    = {'nupl': 100}
ADAPTIVE_BLEND     = 0.7                 # weight on the adaptive (percentile) part; sweep-validated 2026-07-08
ADAPTIVE_WIN_YEARS = 4                   # trailing window for the percentile
ADAPTIVE_DEBUG     = {}                  # per-run breakdown for transparency
_HIST_CACHE        = {}
_UNIFIED_CACHE     = None               # lazy-loaded unified_history series

def _load_unified_cache():
    """Load unified_history.json into a list of dicts, once per process."""
    global _UNIFIED_CACHE
    if _UNIFIED_CACHE is not None:
        return _UNIFIED_CACHE
    path = 'data/history/unified_history.json'
    if os.path.exists(path):
        data = json.load(open(path, encoding='utf-8'))
        _UNIFIED_CACHE = data.get('series', [])
    else:
        _UNIFIED_CACHE = []
    return _UNIFIED_CACHE

def _load_metric_history(metric):
    """Return [(date, value)] from unified_history + recent daily vectors.

    Primary source: data/history/unified_history.json (built by
    tools/build_unified_history.py).  Falls back to individual seed files
    when unified_history.json is absent or missing the metric.
    Daily vector is always appended to capture the most recent runs.
    """
    if metric in _HIST_CACHE:
        return _HIST_CACHE[metric]

    pts = []
    unified_field = _UNIFIED_FIELD.get(metric, metric)

    # ── Primary: unified_history.json ───────────────────────────────────────
    for row in _load_unified_cache():
        v = row.get(unified_field)
        d = row.get('date', '')[:10]
        if d and v is not None:
            pts.append((d, float(v)))

    # ── Special: etf_flows uses a flat-list history file (not series dict) ─────
    if not pts and metric == 'etf_flows':
        etf_path = 'data/history/etf_flows.json'
        try:
            if os.path.exists(etf_path):
                raw_etf = json.load(open(etf_path, encoding='utf-8'))
                if isinstance(raw_etf, list):
                    for r in raw_etf:
                        d = (r.get('timestamp') or r.get('date', ''))[:10]
                        v = r.get('etf_flow_7d')
                        if v is None:
                            v = r.get('etf_flow_14d')
                        if d and v is not None:
                            pts.append((d, float(v)))
        except Exception:
            pass

    # ── Fallback: individual seed file (when unified is missing/empty) ───────
    if not pts:
        seed = f'data/history/{metric}_history.json'
        val_key = _SEED_VAL.get(metric)
        try:
            if os.path.exists(seed):
                for r in json.load(open(seed, encoding='utf-8')).get('series', []):
                    if isinstance(r, list):
                        date, val = str(r[0])[:10], r[1]
                    elif isinstance(r, dict):
                        date = r.get('date', '')[:10]
                        val = r.get(val_key) if val_key else r.get('value')
                    else:
                        continue
                    if date and val is not None:
                        pts.append((date, float(val)))
        except Exception:
            pass

    # ── Tail: daily vector (most recent runs, not yet in unified) ────────────
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

def _percentile_score(metric, value):
    """0-100 rolling percentile of `value` within the trailing window, or None."""
    if value is None:
        return None
    # Unwrap doubly-nested dicts (e.g. mayer_multiple: {value:{value:0.815,...}})
    while isinstance(value, dict):
        value = value.get('value')
    if not isinstance(value, (int, float)):
        return None
    pts = _load_metric_history(metric)
    if len(pts) < 24:                       # need a couple of years of context
        return None
    lo = (datetime.date.fromisoformat(pts[-1][0][:10])
          - datetime.timedelta(days=int(ADAPTIVE_WIN_YEARS * 365))).isoformat()
    win = [v for (d, v) in pts if d[:10] >= lo]
    if len(win) < 12:
        return None
    # Normalize value to match the units stored in history (e.g. NUPL: % → fraction)
    cmp = value / _PCTILE_DIVISOR.get(metric, 1)
    le = sum(1 for v in win if v <= cmp)   # higher value = higher risk
    return round(le / len(win) * 100)

def _adaptive(metric, value, fixed_score):
    """Blend the fixed score with the rolling percentile; record the breakdown.
    Falls back to the pure fixed score when history is insufficient."""
    if fixed_score is None or metric not in ADAPTIVE_METRICS:
        return fixed_score
    pct = _percentile_score(metric, value)
    if pct is None:
        ADAPTIVE_DEBUG[metric] = {'fixed': fixed_score, 'adaptive': None, 'blended': fixed_score}
        return fixed_score
    blended = round(ADAPTIVE_BLEND * pct + (1 - ADAPTIVE_BLEND) * fixed_score)
    ADAPTIVE_DEBUG[metric] = {'fixed': fixed_score, 'adaptive': pct, 'blended': blended,
                              'win_years': ADAPTIVE_WIN_YEARS, 'blend_w': ADAPTIVE_BLEND}
    return blended

OC_WEIGHTS = {
    'rhodl_ratio':         0.20,
    'mvrv_z_score':        0.20,
    'cvdd_ratio':          0.15,
    'nupl':                0.30,
    'asopr':               0.15,
}

TECH_WEIGHTS = {
    'cipherb':             0.40,   # +bearish_div penalty; основний price/momentum сигнал (зменшено з 50% -> 40%)
    'mayer_multiple':      0.20,   # Mayer Multiple (Price / 200DMA)
    'etf_flows':           0.10,   # Spot ETF flows 14d rolling sum (новий тактичний ліквідний індикатор)
    'fear_greed':          0.10,
    'yield_curve_spread':  0.10,   # US 10Y-2Y Spread (T10Y2Y). Inversion (<0) = high risk
    'm2_yoy':              0.10,   # Global M2 YoY (обернена логіка: ↑ ліквідність = ↓ ризик)
}


# ── Slider map functions (same as report.py) ────────────────────────────────

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
    MIN = 0.88
    BASE = 1.00
    MAX = 1.12
    if v <= BASE:
        score = ((v - MIN) / (BASE - MIN)) * 50
    else:
        score = 50 + ((v - BASE) / (MAX - BASE)) * 50
    return round(max(0, min(100, score)))

def map_fear_greed(v):
    if v is None: return None
    if isinstance(v, dict):
        val = v.get('avg_7d') if v.get('avg_7d') is not None else v.get('latest') if v.get('latest') is not None else v.get('value')
        if val is None: return None
        return round(max(0, min(100, val)))
    return round(max(0, min(100, v)))

def map_m2_mom(v):  # legacy — kept for reference only
    """Old inverted 10w momentum mapping. No longer used."""
    if v is None: return None
    v = max(-2, min(4, v))
    return round(((4 - v) / 6) * 100)


def map_m2(v):  # Global M2 YoY %, inverted: high expansion = low risk score
    if v is None: return None
    # Global M2 YoY % change (MacroMicro chart 3439, major central banks combined)
    # HIGH YoY → global liquidity expansion → tailwind for BTC → LOW risk score
    # LOW/negative YoY → global tightening → headwind for BTC → HIGH risk score
    # Range calibrated for global M2: strong expansion floor ~15%, contraction cap ~-2%
    # Normal peacetime growth 4-6% → score 50-65; high growth 9-10% → ~30-33 (low risk)
    v = max(-2, min(15, v))
    return round(((15 - v) / 17) * 100)

def map_yield_curve(v):
    # US 10Y-2Y Yield Curve Spread (T10Y2Y).
    # Deep inversion (<= -1.0%) = 100 risk. Steep healthy curve (>= +2.0%) = 0 risk.
    if v is None: return None
    v = max(-1.0, min(2.0, v))
    return round(((2.0 - v) / 3.0) * 100)

def map_dxy(v):
    """FRED DTWEXBGS (Nominal Broad Dollar Index, Jan 2006 = 100).
    High = strong USD = risk-off = bearish for BTC = high score.
    <= 108 → 0, = 116 → 50, >= 128 → 100.
    """
    if v is None: return None
    v = float(v)
    if v <= 108.0: return 0
    if v >= 128.0: return 100
    if v <= 116.0:
        return round((v - 108.0) / (116.0 - 108.0) * 50)
    return round(50 + (v - 116.0) / (128.0 - 116.0) * 50)

def map_lth_supply(v):
    """LTH Supply % of total BTC supply.
    High % = long-term holders accumulating = bottom territory = low score.
    Low % = distribution = top territory = high score.
    >= 82% → 0, = 68.5% → 50, <= 55% → 100.

    Accepts both percentage (e.g. 76.3) and raw BTC count (e.g. 16_646_824).
    Raw BTC is divided by 21_000_000 (hard cap) to get percentage.
    """
    if v is None: return None
    v = float(v)
    if v > 100:  # raw BTC count — convert to % of hard cap
        v = v / 21_000_000 * 100
    v = max(55.0, min(82.0, v))
    return round(((82.0 - v) / (82.0 - 55.0)) * 100)

def map_mayer_multiple(v):
    """v is mayer_multiple dict or pre-computed score int.
    MM <= 0.5 -> score = 0, MM >= 2.1 -> score = 100.
    """
    if v is None: return None
    if isinstance(v, dict):
        return v.get('score')
    v = float(v)
    v = max(0.5, min(2.1, v))
    return round((v - 0.5) / 1.6 * 100)

def map_funding(v):
    """v is funding_rate dict or avg_7d float (%).
    Range: -0.05% (shorts overheated) .. +0.10% (longs overheated).
    score = clamp((avg + 0.05) / 0.15 * 100, 0, 100)
    """
    if v is None: return None
    if isinstance(v, dict):
        avg = v.get('avg_7d')
        if avg is None: return v.get('score')
        v = avg
    v = float(v)
    return round(max(0, min(100, (v + 0.05) / 0.15 * 100)))

def map_georisk(v):
    if v is None: return None
    v = max(0, min(350, v))
    return round(((350 - v) / 350) * 100)

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
    """
    v is etf_flows dict or pre-computed float/int.
    Calibrated for 7d rolling sum (live scraper produces 7d).
    <= -1000 -> 0 risk
    = 375 -> 50 risk
    >= 2000 -> 100 risk
    Note: percentile path uses _PCTILE_DIVISOR=0.5 to compare 7d live vs 14d history.
    """
    if v is None: return None
    if isinstance(v, dict):
        v = v.get('value')
    if v is None: return None
    v = float(v)

    MIN_VAL = -1000.0
    MID_VAL = 375.0
    MAX_VAL = 2000.0
    
    if v <= MIN_VAL:
        score = 0.0
    elif v >= MAX_VAL:
        score = 100.0
    elif v < MID_VAL:
        score = ((v - MIN_VAL) / (MID_VAL - MIN_VAL)) * 50.0
    else:
        score = 50.0 + ((v - MID_VAL) / (MAX_VAL - MID_VAL)) * 50.0
        
    return round(max(0.0, min(100.0, score)))


def score_from_raw(raw, adaptive_pcts=None):
    """Single source of truth for scoring. Used by both live scraper and backtest.

    raw keys (all optional, None → metric absent):
      nupl              float  percent (-50..100)
      mvrv              float  Z-score
      rhodl_ratio       float  ratio
      cvdd_ratio        float  ratio
      asopr             float  standard scale ~1.0
      mayer_multiple    float or dict
      fear_greed        float  0-100
      m2_yoy            float  YoY %
      cipherb           dict   {weekly_score, daily_score, fast_bearish_div,
                                fast_bullish_div, daily_fast_bearish_div,
                                daily_fast_bullish_div}
      yield_curve_spread float
      etf_flows         float or dict
      funding_rate      float or dict  (optional)

    adaptive_pcts: {metric_key: percentile_0_to_100}
      Supported: 'nupl', 'mvrv', 'cvdd_ratio', 'mayer'
      Score = ADAPTIVE_BLEND*pct + (1-ADAPTIVE_BLEND)*fixed_map_score
    """
    if adaptive_pcts is None:
        adaptive_pcts = {}

    def _blend(key, fixed):
        pct = adaptive_pcts.get(key)
        if pct is None or fixed is None:
            return fixed
        return round(ADAPTIVE_BLEND * pct + (1 - ADAPTIVE_BLEND) * fixed)

    cb = raw.get('cipherb')
    cipherb_score = None
    cb_weekly_raw = None   # raw weekly score, no divergence penalty — for v2 wave vector
    cb_daily_raw  = None   # raw daily score, no penalty — for v2 wave vector
    if isinstance(cb, dict):
        w = cb.get('weekly_score')
        d = cb.get('daily_score')
        cb_weekly_raw = round(w) if w is not None else None
        cb_daily_raw  = round(d) if d is not None else None
        if w is not None:
            if cb.get('fast_bearish_div'):
                w = min(100.0, w + 12)
            elif cb.get('fast_bullish_div'):
                w = max(0.0, w - 12)
            if d is not None:
                if cb.get('daily_fast_bearish_div'):
                    d = min(100.0, d + 12)
                elif cb.get('daily_fast_bullish_div'):
                    d = max(0.0, d - 12)
                cipherb_score = round(0.8 * w + 0.2 * d)
            else:
                cipherb_score = round(w)

    return {
        'nupl':               _blend('nupl',       map_nupl(raw.get('nupl'))),
        'mvrv_z_score':       _blend('mvrv',       map_mvrv(raw.get('mvrv'))),
        'rhodl_ratio':        map_rhodl(raw.get('rhodl_ratio')),
        'cvdd_ratio':         _blend('cvdd_ratio', map_cvdd(raw.get('cvdd_ratio'))),
        'asopr':              map_asopr(raw.get('asopr')),
        'mayer_multiple':     _blend('mayer',      map_mayer_multiple(raw.get('mayer_multiple'))),
        'fear_greed':         map_fear_greed(raw.get('fear_greed')),
        'm2_yoy':             map_m2(raw.get('m2_yoy')),
        'yield_curve_spread': map_yield_curve(raw.get('yield_curve_spread')),
        'cipherb':            cipherb_score,       # combined + penalty (v1 weights)
        'cipherb_weekly':     cb_weekly_raw,        # raw weekly, no penalty (v2 wave vector)
        'cipherb_daily':      cb_daily_raw,         # raw daily, no penalty (v2 wave vector)
        'etf_flows':          _blend('etf_flows', map_etf_flow(raw.get('etf_flows'))),
        'funding_rate':       map_funding(raw.get('funding_rate')),
    }


def build_slider_map(metrics: dict) -> dict:
    """
    Given metrics dict (from data.json['metrics']),
    return {metric_name: slider_value (0-100 or None)}.
    """
    ADAPTIVE_DEBUG.clear()

    def mv(key):
        obj = metrics.get(key)
        if obj is None: return None
        if isinstance(obj, dict) and 'value' in obj:
            return obj['value']
        return obj

    # Compute trailing-window adaptive percentiles and record debug breakdown
    adaptive_pcts = {}
    _fixed_fn = {'nupl': map_nupl, 'mvrv': map_mvrv,
                 'cvdd_ratio': map_cvdd, 'mayer': map_mayer_multiple,
                 'etf_flows': map_etf_flow}
    _raw_key  = {'nupl': 'nupl', 'mvrv': 'mvrv',
                 'cvdd_ratio': 'cvdd_ratio', 'mayer': 'mayer_multiple',
                 'etf_flows': 'etf_flows'}
    for metric in ('nupl', 'mvrv', 'cvdd_ratio', 'mayer', 'etf_flows'):
        val   = mv(_raw_key[metric])
        pct   = _percentile_score(metric, val)
        fixed = _fixed_fn[metric](val)
        if pct is not None and fixed is not None:
            blended = round(ADAPTIVE_BLEND * pct + (1 - ADAPTIVE_BLEND) * fixed)
            ADAPTIVE_DEBUG[metric] = {'fixed': fixed, 'adaptive': pct, 'blended': blended,
                                      'win_years': ADAPTIVE_WIN_YEARS, 'blend_w': ADAPTIVE_BLEND}
            adaptive_pcts[metric] = pct
        elif fixed is not None:
            ADAPTIVE_DEBUG[metric] = {'fixed': fixed, 'adaptive': None, 'blended': fixed}

    raw = {
        'nupl':               mv('nupl'),
        'mvrv':               mv('mvrv'),
        'rhodl_ratio':        mv('rhodl_ratio'),
        'cvdd_ratio':         mv('cvdd_ratio'),
        'asopr':              mv('asopr'),
        'mayer_multiple':     mv('mayer_multiple'),
        'fear_greed':         mv('fear_greed'),
        'm2_yoy':             mv('m2_mom'),           # data.json key is m2_mom
        'yield_curve_spread': mv('yield_curve'),       # data.json key is yield_curve
        'cipherb':            mv('cipherb'),
        'etf_flows':          mv('etf_flows'),
        'funding_rate':       mv('funding_rate'),
    }

    return score_from_raw(raw, adaptive_pcts)


def weighted_score(weights: dict, slider_map: dict):
    """Weighted average, renormalizing over non-null metrics."""
    total_w = 0.0
    total_s = 0.0
    for key, w in weights.items():
        s = slider_map.get(key)
        if s is not None:
            total_s += s * w
            total_w += w
    return round(total_s / total_w) if total_w > 0 else None


_COV_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'mahalanobis_covariance.json')
_covariance_cache = None

def _load_covariance():
    global _covariance_cache
    if _covariance_cache is not None:
        return _covariance_cache
    if os.path.exists(_COV_PATH):
        try:
            with open(_COV_PATH, 'r', encoding='utf-8') as f:
                _covariance_cache = json.load(f)
        except Exception:
            _covariance_cache = {}
    else:
        _covariance_cache = {}
    return _covariance_cache


def _oc_coherence(sm: dict) -> float:
    """Measure phase synchrony of on-chain metrics using Mahalanobis distance.
    Returns [0, 1]: 1 = all metrics in agreement, 0 = maximally dispersed.
    Used downstream to dampen the final score when OC signals conflict.
    """
    cov_data = _load_covariance()
    if not cov_data or 'covariance' not in cov_data or 'keys' not in cov_data:
        # Fallback to old weighted standard deviation coherence
        keys = [k for k in OC_WEIGHTS if sm.get(k) is not None]
        if len(keys) < 3:
            return 1.0
        weights = [OC_WEIGHTS[k] for k in keys]
        vals    = [sm[k] / 100.0 for k in keys]
        total_w = sum(weights)
        mean_v  = sum(w * v for w, v in zip(weights, vals)) / total_w
        var     = sum(w * (v - mean_v) ** 2 for w, v in zip(weights, vals)) / total_w
        # 0.289 = 1/(2√3) = theoretical max std for uniform [0,1] distribution
        return max(0.0, 1.0 - var ** 0.5 / 0.289)

    oc_keys = cov_data['keys']
    K = [k for k in oc_keys if sm.get(k) is not None]
    n = len(K)
    if n < 3:
        return 1.0

    x_K = [sm[k] / 100.0 for k in K]

    # Sub-covariance matrix
    indices = [oc_keys.index(k) for k in K]
    Sigma_K = []
    for r in indices:
        row_cov = [cov_data['covariance'][r][c] for c in indices]
        Sigma_K.append(row_cov)

    # 1. Get orthogonal basis (Orthonormal basis V_K of shape n x (n-1) orthogonal to [1,1,...,1]^T)
    e = [1.0] * n
    norm_e = math.sqrt(n)
    u1 = [1.0 / norm_e] * n
    
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
            
    V_K = []
    for r in range(n):
        row = [all_vectors[c][r] for c in range(1, n)]
        V_K.append(row)

    # 2. Project x_K to y of length n-1
    y = []
    for col in range(n - 1):
        val = sum(V_K[row_idx][col] * x_K[row_idx] for row_idx in range(n))
        y.append(val)

    # 3. Projected covariance Sigma_y = V_K^T * Sigma_K * V_K
    temp = []
    for r in range(n):
        row_temp = []
        for c in range(n - 1):
            val = sum(Sigma_K[r][i] * V_K[i][c] for i in range(n))
            row_temp.append(val)
        temp.append(row_temp)
        
    Sigma_y = []
    for r in range(n - 1):
        row_y = []
        for c in range(n - 1):
            val = sum(V_K[i][r] * temp[i][c] for i in range(n))
            row_y.append(val)
        Sigma_y.append(row_y)

    # 4. Invert Sigma_y (Gaussian elimination)
    # Augment Sigma_y with Identity
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n - 1)] for i, row in enumerate(Sigma_y)]
    for i in range(n - 1):
        pivot_row = i
        for r in range(i + 1, n - 1):
            if abs(aug[r][i]) > abs(aug[pivot_row][i]):
                pivot_row = r
        if abs(aug[pivot_row][i]) < 1e-9:
            # singular matrix fallback
            return 1.0
        aug[i], aug[pivot_row] = aug[pivot_row], aug[i]
        factor = aug[i][i]
        aug[i] = [val / factor for val in aug[i]]
        for r in range(n - 1):
            if r != i:
                factor = aug[r][i]
                aug[r] = [val_r - factor * val_i for val_r, val_i in zip(aug[r], aug[i])]
                
    inv_Sigma_y = [row[(n - 1):] for row in aug]

    # 5. D_M^2 = y^T * inv_Sigma_y * y
    temp_y = []
    for r in range(n - 1):
        val = sum(inv_Sigma_y[r][c] * y[c] for c in range(n - 1))
        temp_y.append(val)
    dm_sq = sum(y[i] * temp_y[i] for i in range(n - 1))
    dm = math.sqrt(max(0.0, dm_sq))

    # 6. Map Mahalanobis distance to coherence in [0, 1]
    # Median is ~1.99, 95% is ~3.14. Scale dm=1.0 -> 1.0, dm=3.5 -> 0.0
    coherence = 1.0 - (dm - 1.0) / 2.5
    return max(0.0, min(1.0, coherence))



# ── Fisher-weighted scoring (data-driven weights) ─────────────────────────

import json as _json
import os as _os

_FISHER_PATH = _os.path.join(_os.path.dirname(__file__), '..', 'data', 'fisher_weights.json')
_fisher_cache = {}

def _load_fisher_weights():
    if _fisher_cache:
        return _fisher_cache
    try:
        with open(_FISHER_PATH, encoding='utf-8') as f:
            d = _json.load(f)
        _fisher_cache.update(d.get('weights', {}))
    except Exception:
        pass
    return _fisher_cache


def compute_scores_v2_fisher(metrics: dict) -> dict:
    """
    Fisher-weighted scoring: single metric pool, data-driven weights.
    Returns dict with final_score, oc_coherence, weights_used.
    Returns None if fisher_weights.json not found.
    """
    weights = _load_fisher_weights()
    if not weights:
        return None
    sm = build_slider_map(metrics)
    raw_score = weighted_score(weights, sm)
    oc_coh = _oc_coherence(sm)
    return {
        'final_score':  raw_score,
        'oc_coherence': oc_coh,
        'weights_used': dict(weights),
    }


def compute_scores(metrics: dict) -> dict:
    """
    Returns {'onchain_score', 'tech_score', 'final_score',
             'onchain_avg', 'tech_avg', 'oc_coherence'}.
    """
    sm = build_slider_map(metrics)
    oc_avg   = weighted_score(OC_WEIGHTS,   sm)
    tech_avg = weighted_score(TECH_WEIGHTS, sm)

    def blend(oc, tech, oc_w):
        if oc is None and tech is None: return None
        if oc is None:   return round(tech)
        if tech is None: return round(oc)
        return round(oc * oc_w + tech * (1 - oc_w))

    return {
        'onchain_avg':    oc_avg,
        'tech_avg':       tech_avg,
        'onchain_score':  blend(oc_avg, tech_avg, 0.80),
        'tech_score':     blend(oc_avg, tech_avg, 0.20),
        'final_score':    blend(oc_avg, tech_avg, 0.50),
        'adaptive':       dict(ADAPTIVE_DEBUG),
        'oc_coherence':   round(_oc_coherence(sm), 3),
    }
