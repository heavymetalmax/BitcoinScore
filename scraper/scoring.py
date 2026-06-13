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
ADAPTIVE_METRICS   = {'nupl', 'mvrv', 'mayer', 'cvdd_ratio', 'puell'}
# daily_vector raw key differs from the adaptive metric name for some metrics
_DV_KEY            = {'mayer': 'mayer_multiple', 'cvdd_ratio': 'cvdd_ratio'}
# field name in unified_history.json when it differs from the metric key
_UNIFIED_FIELD     = {'mayer': 'mayer_multiple', 'cvdd_ratio': 'cvdd_ratio'}
# seed history files that use dict rows: maps metric → value key inside the dict
_SEED_VAL          = {'cvdd_ratio': 'ratio'}
# unified_history/seed files store NUPL as fraction (0–1); data.json uses % (0–100).
# Divide incoming value by this factor before percentile comparison so units match.
_PCTILE_DIVISOR    = {'nupl': 100}
ADAPTIVE_BLEND     = 0.5                 # weight on the adaptive (percentile) part
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
    v = max(-50, min(100, v))
    return round(((v + 50) / 150) * 100)

def map_mvrv(v):
    if v is None: return None
    # Range calibrated to current-era max Z-score ~5 (2013/2017 hit >10, no longer realistic)
    v = max(-2, min(5, v))
    return round(((v + 2) / 7) * 100)

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
    # Range calibrated to 10000 (2021 hit 100K historically, but 2024+ cycles cap ~8K)
    v = max(100, min(10000, v))
    return round((math.log10(v) - math.log10(100)) / (math.log10(10000) - math.log10(100)) * 100)

def map_etf_flow(v):
    """
    v is etf_flows dict or pre-computed float/int.
    14d flow sum:
    <= -2000 -> 0 risk
    = 750 -> 50 risk
    >= 4000 -> 100 risk
    """
    if v is None: return None
    if isinstance(v, dict):
        v = v.get('value')
    if v is None: return None
    v = float(v)
    
    MIN_VAL = -2000.0
    MID_VAL = 750.0
    MAX_VAL = 4000.0
    
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
        'etf_flows':          map_etf_flow(raw.get('etf_flows')),
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
                 'cvdd_ratio': map_cvdd, 'mayer': map_mayer_multiple}
    _raw_key  = {'nupl': 'nupl', 'mvrv': 'mvrv',
                 'cvdd_ratio': 'cvdd_ratio', 'mayer': 'mayer_multiple'}
    for metric in ('nupl', 'mvrv', 'cvdd_ratio', 'mayer'):
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


def _oc_coherence(sm: dict) -> float:
    """Measure phase synchrony of on-chain metrics.
    Returns [0, 1]: 1 = all metrics in agreement, 0 = maximally dispersed.
    Used downstream (scoring_v2) to dampen the final score when OC signals conflict.
    """
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
