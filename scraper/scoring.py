"""
Decision matrix scoring — shared between report.py and scraper.py.

On-chain group  (5 metrics, weights sum to 1.0):
  rhodl_ratio ×20  mvrv_z_score ×20  cvdd_ratio ×20  nupl ×20  asopr ×20

Tech/Macro group  (5 metrics, weights sum to 1.0):
  cipherb ×50  smc ×10  fear_greed ×20  real_yield ×10  m2_yoy ×10
  (cipherb — price/momentum осцилятор; +12 penalty при активній bearish divergence;
   smc — ретроспективний, знижено з ×25 → ×10;
    yield_curve_spread = US 10Y-2Y Yield Curve (T10Y2Y, FRED), обернена логіка: ↓ спред (інверсія) = ↑ ризик;
    m2_yoy = US M2 WM2NS YoY % change (FRED), обернена логіка: ↑ ліквідність = ↓ ризик;

Index 1 (onchain_score) = 80% OC + 20% Tech
Index 2 (tech_score)    = 20% OC + 80% Tech
Final score             = 50% OC + 50% Tech
"""

import math

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
    'm2_yoy':              0.10,   # US M2 YoY (обернена логіка: ↑ ліквідність = ↓ ризик)
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


def map_m2(v):  # US M2 YoY, inverted: high expansion = low risk score
    if v is None: return None
    # US M2 year-over-year % change (FRED WM2NS)
    # HIGH YoY → system flooded with liquidity → low macro risk → LOW score
    # LOW/negative YoY → liquidity tightening → high macro risk → HIGH score
    # Range: -5% to +10%
    v = max(-5, min(10, v))
    return round(((10 - v) / 15) * 100)

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


def build_slider_map(metrics: dict) -> dict:
    """
    Given metrics dict (from data.json['metrics']),
    return {metric_name: slider_value (0-100 or None)}.
    """
    def mv(key):
        obj = metrics.get(key)
        if obj is None: return None
        if isinstance(obj, dict) and 'value' in obj:
            return obj['value']
        return obj

    cipherb = mv('cipherb')
    cipherb_score = None
    if isinstance(cipherb, dict):
        w_score = cipherb.get('weekly_score')
        d_score = cipherb.get('daily_score')
        if w_score is not None:
            if cipherb.get('fast_bearish_div'):
                w_score = min(100.0, w_score + 12)
            elif cipherb.get('fast_bullish_div'):
                w_score = max(0.0, w_score - 12)
            
            if d_score is not None:
                if cipherb.get('daily_fast_bearish_div'):
                    d_score = min(100.0, d_score + 12)
                elif cipherb.get('daily_fast_bullish_div'):
                    d_score = max(0.0, d_score - 12)
                cipherb_score = round(0.8 * w_score + 0.2 * d_score)
            else:
                cipherb_score = round(w_score)

    mayer_val = mv('mayer_multiple')
    mayer_score = map_mayer_multiple(mayer_val)

    return {
        'nupl':                map_nupl(mv('nupl')),
        'mvrv_z_score':        map_mvrv(mv('mvrv')),
        'fear_greed':          map_fear_greed(mv('fear_greed')),
        'm2_yoy':              map_m2(mv('m2_mom')),  # field key in data.json still 'm2_mom'
        'yield_curve_spread':  map_yield_curve(mv('yield_curve')),
        'cvdd_ratio':          map_cvdd(mv('cvdd_ratio')),
        'rhodl_ratio':         map_rhodl(mv('rhodl_ratio')),
        'asopr':               map_asopr(mv('asopr')),
        'etf_flows':           map_etf_flow(mv('etf_flows')),
        'cipherb':             cipherb_score,
        'mayer_multiple':      mayer_score,
        'funding_rate':        map_funding(mv('funding_rate')),
    }


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


def compute_scores(metrics: dict) -> dict:
    """
    Returns {'onchain_score', 'tech_score', 'final_score',
             'onchain_avg', 'tech_avg'}.
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
    }
