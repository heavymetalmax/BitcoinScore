"""
Decision matrix scoring — shared between report.py and scraper.py.

On-chain group  (6 metrics, weights sum to 1.0):
  nupl ×22  mvrv_z_score ×14  addresses_in_profit ×24  cvdd_ratio ×18  rhodl_ratio ×12  sopr ×10
  (addr_in_profit ↑ — цикло-нейтральний; mvrv/rhodl ↓ — деградують з ростом realized cap)

Tech/Macro group  (6 metrics, weights sum to 1.0):
  cipherb ×40  smc ×28  m2_yoy ×8  fear_greed ×12  dxy ×8  geopolitical_risk ×4
  (smc ↑ — структурний індикатор; fear_greed ↓ — занижений в інституційних циклах)
  (m2_yoy = Global M2 YoY % change; source: BMP Global Liquidity index)

Index 1 (onchain_score) = 60% OC + 40% Tech
Index 2 (tech_score)    = 40% OC + 60% Tech
Final score             = 50% OC + 50% Tech
"""

import math

OC_WEIGHTS = {
    'nupl':                0.22,
    'mvrv_z_score':        0.14,
    'cvdd_ratio':          0.18,
    'rhodl_ratio':         0.12,
    'sopr':                0.10,
    'addresses_in_profit': 0.24,
}

TECH_WEIGHTS = {
    'cipherb':             0.40,
    'smc':                 0.28,   # ↑ від 0.20 — структурний індикатор, цикло-нейтральний
    'm2_mom':              0.08,
    'fear_greed':          0.12,   # ↓ від 0.20 — роздрібний сентимент деградує в інст. циклах
    'dxy':                 0.08,
    'geopolitical_risk':   0.04,
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
    return round(max(0, min(100, v)))

def map_m2(v):
    if v is None: return None
    # US M2 10-week momentum (% change): leading indicator for BTC ~10 weeks ahead
    # HIGH momentum → BTC will rise → accumulate now → LOW score
    # LOW/negative momentum → BTC will fall → exit now → HIGH score  (inverted)
    # Range: -2% (QT contraction) to +4% (strong expansion)
    v = max(-2, min(4, v))
    return round(((4 - v) / 6) * 100)

def map_dxy(v):
    if v is None: return None
    v = max(80, min(160, v))
    return round(((160 - v) / 80) * 100)

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

    dxy_raw     = mv('dxy')
    georisk_raw = mv('geopolitical_risk')
    dxy_val     = dxy_raw[0] if isinstance(dxy_raw, (list, tuple)) else (dxy_raw.get('current') if isinstance(dxy_raw, dict) else dxy_raw)
    georisk_val = georisk_raw[0] if isinstance(georisk_raw, (list, tuple)) else (georisk_raw.get('current') if isinstance(georisk_raw, dict) else georisk_raw)

    cipherb = mv('cipherb')
    cipherb_score = round(cipherb['weekly_score']) if isinstance(cipherb, dict) and cipherb.get('weekly_score') is not None else None

    smc_val = mv('smc')
    smc_score = round(smc_val['position']) if isinstance(smc_val, dict) and smc_val.get('position') is not None else None

    return {
        'nupl':                map_nupl(mv('nupl')),
        'mvrv_z_score':        map_mvrv(mv('mvrv')),
        'sopr':                map_sopr(mv('sopr')),
        'addresses_in_profit': map_addr_profit(mv('addresses_in_profit')),
        'fear_greed':          map_fear_greed(mv('fear_greed')),
        'm2_mom':              map_m2(mv('m2_mom')),
        'dxy':                 map_dxy(dxy_val),
        'geopolitical_risk':   map_georisk(georisk_val),
        'cvdd_ratio':          map_cvdd(mv('cvdd_ratio')),
        'rhodl_ratio':         map_rhodl(mv('rhodl_ratio')),
        'cipherb':             cipherb_score,
        'smc':                 smc_score,
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
        'onchain_score':  blend(oc_avg, tech_avg, 0.60),
        'tech_score':     blend(oc_avg, tech_avg, 0.40),
        'final_score':    blend(oc_avg, tech_avg, 0.50),
    }
