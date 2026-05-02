"""
Decision matrix scoring — shared between report.py and scraper.py.

On-chain group  (5 metrics, weights sum to 1.0):
  addresses_in_profit ×25  rhodl_ratio ×22  mvrv_z_score ×20  cvdd_ratio ×18  nupl ×15
  (addr_in_profit — прямий % холдерів у прибутку, найстабільніший;
   rhodl ↑ — найнадійніший цикловий маркер, не деградує з ростом realized cap;
   nupl ↓ — корелює з mvrv, зменшено дублювання;
   sopr — видалено: <10% поріг, шум у циклах 2024–2025)

Tech/Macro group  (5 metrics, weights sum to 1.0):
  cipherb ×50  smc ×10  fear_greed ×20  real_yield ×10  m2_yoy ×10
  (cipherb — price/momentum осцилятор; +12 penalty при активній bearish divergence;
   smc — ретроспективний, знижено з ×25 → ×10;
   real_yield = US 10Y TIPS real yield (DFII10, FRED), пряма логіка: ↑ реальна ставка = ↑ ризик;
   m2_yoy = US M2 WM2NS YoY % change (FRED), пряма логіка: ↑ ліквідність = ↑ ризик;
   geopolitical_risk — видалено: <10% поріг, відсутній у backtest)

Index 1 (onchain_score) = 80% OC + 20% Tech
Index 2 (tech_score)    = 20% OC + 80% Tech
Final score             = 50% OC + 50% Tech
"""

import math

OC_WEIGHTS = {
    'addresses_in_profit': 0.25,   # #1 — прямий % холдерів у прибутку
    'rhodl_ratio':         0.22,   # #2 — не деградує з ростом realized cap
    'mvrv_z_score':        0.20,   # #3 — нормований на волатильність
    'cvdd_ratio':          0.18,   # #4
    'nupl':                0.15,   # #5 — корелює з MVRV, менша вага
    # sopr видалено: <10% поріг, слабка диференціація в циклах 2024–2025
}

TECH_WEIGHTS = {
    'cipherb':             0.50,   # +bearish_div penalty; основний price/momentum сигнал
    'pi_cycle':            0.10,   # Pi Cycle Top (111DMA vs 2×350DMA); наближення до cross = max ризик
    'fear_greed':          0.20,
    'real_yield':          0.10,   # US 10Y TIPS real yield (DFII10, FRED)
    'm2_yoy':              0.10,   # US M2 YoY (пряма логіка: ↑ ліквідність = ↑ ризик)
    # geopolitical_risk видалено: <10% поріг, відсутній у backtest
    # smc видалено: 40% false bottom rate, слабка диференціація в ведмедячих трендах
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

def map_sopr(v):
    if v is None: return None
    v = max(-0.05, min(0.10, v))
    return round((v + 0.05) / 0.15 * 100)

def map_addr_profit(v):
    if v is None: return None
    return round(max(0, min(100, v)))

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


def map_m2(v):  # US M2 YoY, direct: high expansion = high risk score
    if v is None: return None
    # US M2 year-over-year % change (FRED WM2NS)
    # HIGH YoY → system flooded with liquidity → market overheated → HIGH score
    # LOW/negative YoY → liquidity tightening → accumulate → LOW score
    # Range: -5% to +20% (excludes 2020 COVID QE outlier +27%)
    v = max(-5, min(20, v))
    return round(((v + 5) / 25) * 100)

def map_real_yield(v):
    # US 10Y Real Yield (DFII10). Direct: high real yield = tight money = high risk
    # Range: -2% (stimulative) to +3% (restrictive)
    if v is None: return None
    v = max(-2, min(3, v))
    return round(((v + 2) / 5) * 100)

def map_pi_cycle(v):
    """v is pi_cycle dict or pre-computed score int.
    gap_pct=50% → score=0 (far from top), gap_pct=0% → score=100 (cross).
    """
    if v is None: return None
    if isinstance(v, dict):
        return v.get('score')
    return round(max(0, min(100, v)))

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
    return round(((v - 1) / 4) * 100)

def map_rhodl(v):
    if v is None: return None
    # Range calibrated to 10000 (2021 hit 100K historically, but 2024+ cycles cap ~8K)
    v = max(100, min(10000, v))
    return round((math.log10(v) - math.log10(100)) / (math.log10(10000) - math.log10(100)) * 100)


def build_slider_map(metrics: dict) -> dict:
    """
    Given metrics dict (from data.json['metrics']),
    return {metric_name: slider_value (0-100 or None)}.
    """
    def mv(key):
        return metrics.get(key, {}).get('value')

    georisk_raw = mv('geopolitical_risk')
    georisk_val = georisk_raw[0] if isinstance(georisk_raw, (list, tuple)) else (georisk_raw.get('current') if isinstance(georisk_raw, dict) else georisk_raw)

    cipherb = mv('cipherb')
    cipherb_score = round(cipherb['weekly_score']) if isinstance(cipherb, dict) and cipherb.get('weekly_score') is not None else None
    if cipherb_score is not None and isinstance(cipherb, dict):
        if cipherb.get('bearish_divergence'):
            cipherb_score = min(100, cipherb_score + 12)  # overbought signal
        elif cipherb.get('bullish_divergence'):
            cipherb_score = max(0, cipherb_score - 12)    # oversold signal

    pi_cycle_val = mv('pi_cycle')
    pi_cycle_score = map_pi_cycle(pi_cycle_val)

    return {
        'nupl':                map_nupl(mv('nupl')),
        'mvrv_z_score':        map_mvrv(mv('mvrv')),
        'sopr':                map_sopr(mv('sopr')),
        'addresses_in_profit': map_addr_profit(mv('addresses_in_profit')),
        'fear_greed':          map_fear_greed(mv('fear_greed')),
        'm2_yoy':              map_m2(mv('m2_mom')),  # field key in data.json still 'm2_mom'
        'real_yield':          map_real_yield(mv('real_yield')),
        'geopolitical_risk':   map_georisk(georisk_val),
        'cvdd_ratio':          map_cvdd(mv('cvdd_ratio')),
        'rhodl_ratio':         map_rhodl(mv('rhodl_ratio')),
        'cipherb':             cipherb_score,
        'pi_cycle':            pi_cycle_score,
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
