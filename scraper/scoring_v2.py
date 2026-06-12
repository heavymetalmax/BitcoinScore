"""Experimental scoring v2 — regime-based weights + Time-in-Zone metric.

Architecture:
  Pass 1: compute prelim_score using standard v1 weights (no TiZ)
  Detect regime: bottom (≤40) / top (≥65) / neutral
  Pass 2: recompute using regime-appropriate weights + TiZ (bottom only)

On-chain groups (weights sum to 1.0):
  BOTTOM:  NUPL×35  MVRV×25  Puell×15  RHODL×15  CVDD×5   aSOPR×5
  TOP:     NUPL×35  MVRV×25  RHODL×25  CVDD×10   aSOPR×5
  NEUTRAL: NUPL×30  MVRV×20  RHODL×20  CVDD×15   aSOPR×15  (same as v1)

Tech groups (weights sum to 1.0):
  BOTTOM:  CipherB×25  Mayer×20  FearGreed×15  ETF×15  YieldCurve×15  M2×10
  TOP:     CipherB×55  Mayer×20  FearGreed×10  ETF×5   YieldCurve×5   M2×5
  NEUTRAL: CipherB×40  Mayer×20  FearGreed×10  ETF×10  YieldCurve×10  M2×10

Final blending:
  BOTTOM:  0.40*OC + 0.40*Tech + 0.20*TiZ
  TOP:     0.50*OC + 0.50*Tech
  NEUTRAL: 0.50*OC + 0.50*Tech
"""
import math
from .scoring import (
    build_slider_map, weighted_score, compute_scores,
    ADAPTIVE_DEBUG,
    map_nupl, map_mvrv, map_rhodl, map_cvdd, map_asopr,
    map_fear_greed, map_m2, map_yield_curve, map_mayer_multiple,
    map_etf_flow, map_funding,
)
from .tiz import compute_tiz

# ── Regime thresholds ────────────────────────────────────────────────────────
BOTTOM_THRESHOLD = 40
TOP_THRESHOLD    = 65

# ── Weight tables ────────────────────────────────────────────────────────────
OC_BOTTOM = {
    'nupl':         0.35,
    'mvrv_z_score': 0.25,
    'puell':        0.15,
    'rhodl_ratio':  0.15,
    'cvdd_ratio':   0.05,
    'asopr':        0.05,
}
OC_TOP = {
    'nupl':         0.35,
    'mvrv_z_score': 0.25,
    'rhodl_ratio':  0.25,
    'cvdd_ratio':   0.10,
    'asopr':        0.05,
}
OC_NEUTRAL = {
    'nupl':         0.30,
    'mvrv_z_score': 0.20,
    'rhodl_ratio':  0.20,
    'cvdd_ratio':   0.15,
    'asopr':        0.15,
}
TECH_BOTTOM = {
    'cipherb':           0.25,
    'mayer_multiple':    0.20,
    'fear_greed':        0.15,
    'etf_flows':         0.15,
    'yield_curve_spread':0.15,
    'm2_yoy':            0.10,
}
TECH_TOP = {
    'cipherb':           0.55,
    'mayer_multiple':    0.20,
    'fear_greed':        0.10,
    'etf_flows':         0.05,
    'yield_curve_spread':0.05,
    'm2_yoy':            0.05,
}
TECH_NEUTRAL = {
    'cipherb':           0.40,
    'mayer_multiple':    0.20,
    'fear_greed':        0.10,
    'etf_flows':         0.10,
    'yield_curve_spread':0.10,
    'm2_yoy':            0.10,
}


def map_puell(v):
    """Puell Multiple → 0-100 risk score.
    < 0.5  → bottom zone (0-15)
    0.5-1  → accumulation (15-40)
    1-2    → neutral (40-65)
    2-4    → bull market (65-90)
    > 4    → overheated top (90-100)
    """
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get('value')
    if v is None:
        return None
    v = float(v)
    if v <= 0.5:
        return round(max(0, v / 0.5 * 15))
    if v <= 1.0:
        return round(15 + (v - 0.5) / 0.5 * 25)
    if v <= 2.0:
        return round(40 + (v - 1.0) / 1.0 * 25)
    if v <= 4.0:
        return round(65 + (v - 2.0) / 2.0 * 25)
    return 100


def build_slider_map_v2(metrics: dict) -> dict:
    """Extend v1 slider map with Puell Multiple."""
    sm = build_slider_map(metrics)

    def mv(key):
        obj = metrics.get(key)
        if obj is None: return None
        if isinstance(obj, dict) and 'value' in obj:
            return obj['value']
        return obj

    sm['puell'] = map_puell(mv('puell_multiple'))
    return sm


def compute_scores_v2(metrics: dict) -> dict:
    """Two-pass regime-aware scoring.

    Returns v1 keys plus:
      regime         — 'bottom' | 'neutral' | 'top'
      tiz_score      — TiZ risk value (int) or None
      tiz_days       — days in zone (int)
      oc_weights     — which OC weight set was used
    """
    # ── Pass 1: prelim with v1 weights ──────────────────────────────────────
    v1 = compute_scores(metrics)
    prelim = v1['final_score']

    if prelim is None:
        return {**v1, 'regime': 'neutral', 'tiz_score': None, 'tiz_days': 0}

    # ── Detect regime ────────────────────────────────────────────────────────
    if prelim <= BOTTOM_THRESHOLD:
        regime = 'bottom'
        oc_w, tech_w = OC_BOTTOM, TECH_BOTTOM
    elif prelim >= TOP_THRESHOLD:
        regime = 'top'
        oc_w, tech_w = OC_TOP, TECH_TOP
    else:
        regime = 'neutral'
        oc_w, tech_w = OC_NEUTRAL, TECH_NEUTRAL

    # ── Pass 2: regime weights ───────────────────────────────────────────────
    sm = build_slider_map_v2(metrics)
    oc_avg   = weighted_score(oc_w,   sm)
    tech_avg = weighted_score(tech_w, sm)

    # ── TiZ (bottom regime only) ─────────────────────────────────────────────
    tiz_score, tiz_days = compute_tiz() if regime == 'bottom' else (None, 0)

    # ── Final blend ──────────────────────────────────────────────────────────
    def safe(v): return v if v is not None else 0

    if regime == 'bottom' and tiz_score is not None:
        final = round(0.40 * safe(oc_avg) + 0.40 * safe(tech_avg) + 0.20 * tiz_score)
    else:
        parts = [x for x in [oc_avg, tech_avg] if x is not None]
        final = round(sum(parts) / len(parts)) if parts else None

    def blend(oc, tech, oc_w_ratio):
        if oc is None and tech is None: return None
        if oc is None:   return round(tech)
        if tech is None: return round(oc)
        return round(oc * oc_w_ratio + tech * (1 - oc_w_ratio))

    return {
        'onchain_avg':    oc_avg,
        'tech_avg':       tech_avg,
        'onchain_score':  blend(oc_avg, tech_avg, 0.80),
        'tech_score':     blend(oc_avg, tech_avg, 0.20),
        'final_score':    final,
        'adaptive':       dict(ADAPTIVE_DEBUG),
        'regime':         regime,
        'tiz_score':      tiz_score,
        'tiz_days':       tiz_days,
        'oc_weights':     list(oc_w.keys()),
    }


__all__ = ['compute_scores_v2', 'map_puell']
