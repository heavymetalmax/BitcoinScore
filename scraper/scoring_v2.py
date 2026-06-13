"""Experimental scoring v2 — regime-based weights + Time-in-Zone + adaptive CipherB.

Architecture:
  Pass 1: compute prelim_score using standard v1 weights (no TiZ)
  Detect regime: bottom (≤40) / top (≥65) / neutral
  Pass 2: recompute using regime-appropriate weights + TiZ (bottom only)
          CipherB blend is also regime+crash-aware (see adaptive_cipherb_blend)

On-chain groups (weights sum to 1.0):
  BOTTOM:  NUPL×35  MVRV×25  Puell×15  RHODL×15  CVDD×5   aSOPR×5
  TOP:     NUPL×35  MVRV×25  RHODL×25  CVDD×10   aSOPR×5
  NEUTRAL: NUPL×30  MVRV×20  RHODL×20  CVDD×15   aSOPR×15  (same as v1)

Tech groups (weights sum to 1.0):
  BOTTOM:  CipherB×25  Mayer×20  FearGreed×15  ETF×15  YieldCurve×15  M2×10
  TOP:     CipherB×55  Mayer×20  FearGreed×10  ETF×5   YieldCurve×5   M2×5
  NEUTRAL: CipherB×40  Mayer×20  FearGreed×10  ETF×10  YieldCurve×10  M2×10

Adaptive CipherB blend (weekly weight):
  top    → 1.00  (pure weekly — perfect top capture)
  crash  → 0.40  (lean daily — sudden crash capture)
  other  → 0.80  (standard blend)
  crash = (weekly_adj - daily_adj > 25) AND (daily_adj < 20)

Final blending:
  BOTTOM:  0.40*OC + 0.40*Tech + 0.20*TiZ
  TOP:     0.50*OC + 0.50*Tech
  NEUTRAL: 0.50*OC + 0.50*Tech
"""
import math
from .scoring import (
    build_slider_map, weighted_score, compute_scores,
    ADAPTIVE_DEBUG, _adaptive,
    map_nupl, map_mvrv, map_rhodl, map_cvdd, map_asopr,
    map_fear_greed, map_m2, map_yield_curve, map_mayer_multiple,
    map_etf_flow, map_funding,
)
from .tiz import compute_tiz, _CALIBRATION as _TIZ_CALIBRATION
from .wave_resonance import compute_wave_resonance
from .orchestrator import orchestrate

# ── Regime thresholds ────────────────────────────────────────────────────────
BOTTOM_THRESHOLD = 40
TOP_THRESHOLD    = 65

# ── Crash detection parameters ───────────────────────────────────────────────
_CRASH_DIV_THRESHOLD = 25   # weekly_adj - daily_adj gap that signals sudden crash
_CRASH_DAILY_MAX     = 20   # daily must also be below this to confirm crash

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


def _cipherb_adjusted_scores(metrics):
    """Return (weekly_adj, daily_adj) with divergence penalties already applied."""
    cipherb = metrics.get('cipherb')
    if not isinstance(cipherb, dict):
        return None, None
    w = cipherb.get('weekly_score')
    d = cipherb.get('daily_score')
    if w is not None:
        if cipherb.get('fast_bearish_div'):
            w = min(100.0, w + 12)
        elif cipherb.get('fast_bullish_div'):
            w = max(0.0, w - 12)
    if d is not None:
        if cipherb.get('daily_fast_bearish_div'):
            d = min(100.0, d + 12)
        elif cipherb.get('daily_fast_bullish_div'):
            d = max(0.0, d - 12)
    return w, d


def adaptive_cipherb_blend(metrics, regime):
    """CipherB score with regime+crash-aware weekly/daily blend.

    Blend ratios (weekly weight):
      top   → 1.00  pure weekly — best top detection
      crash → 0.40  lean daily  — capture sudden drops
      other → 0.80  standard
    """
    w, d = _cipherb_adjusted_scores(metrics)
    if w is None:
        return round(d) if d is not None else None
    if d is None:
        return round(w)
    is_crash = (w - d) > _CRASH_DIV_THRESHOLD and d < _CRASH_DAILY_MAX
    if regime == 'top':
        ratio = 1.0
    elif is_crash:
        ratio = 0.40
    else:
        ratio = 0.80
    return round(ratio * w + (1 - ratio) * d)


def build_slider_map_v2(metrics: dict) -> dict:
    """Extend v1 slider map with Puell Multiple (adaptive-calibrated)."""
    sm = build_slider_map(metrics)

    def mv(key):
        obj = metrics.get(key)
        if obj is None: return None
        if isinstance(obj, dict) and 'value' in obj:
            return obj['value']
        return obj

    puell_raw = mv('puell_multiple')
    sm['puell'] = _adaptive('puell', puell_raw, map_puell(puell_raw))
    return sm


def compute_scores_v2(metrics: dict) -> dict:
    """Two-pass regime-aware scoring.

    Returns v1 keys plus:
      regime         — 'bottom' | 'neutral' | 'top'
      tiz_score      — TiZ risk value (int) or None
      tiz_days       — days in zone (int)
      oc_weights     — which OC weight set was used
      oc_coherence   — [0,1] synchrony of on-chain metrics (from v1)
      coh_factor     — [regime_floor,1.0] coherence dampening (floor: bottom=0.60, top=0.30, neutral=0.45)
      pi_cross        — bool: Pi Cycle lines crossed (confirmed top override)
    """
    # ── Pass 1: prelim with v1 weights ──────────────────────────────────────
    v1 = compute_scores(metrics)
    prelim = v1['final_score']
    oc_coherence = v1.get('oc_coherence', 1.0)

    if prelim is None:
        return {**v1, 'regime': 'neutral', 'tiz_score': None, 'tiz_days': 0,
                'coh_factor': 1.0, 'pi_cross': False}

    # ── Detect regime (uses undampened prelim for clean threshold logic) ─────
    if prelim <= BOTTOM_THRESHOLD:
        regime = 'bottom'
        oc_w, tech_w = OC_BOTTOM, TECH_BOTTOM
    elif prelim >= TOP_THRESHOLD:
        regime = 'top'
        oc_w, tech_w = OC_TOP, TECH_TOP
    else:
        regime = 'neutral'
        oc_w, tech_w = OC_NEUTRAL, TECH_NEUTRAL

    # ── Pi Cycle binary override ─────────────────────────────────────────────
    # When 111DMA / 350DMA×2 lines cross, it's a confirmed top regardless of score.
    # Research (final_synthesis.md): Apr 2021 cross correctly called the top;
    # the score alone missed it. Override: push final to at least 85.
    pi_raw = metrics.get('pi_cycle') or {}
    if isinstance(pi_raw, dict) and 'value' in pi_raw:
        pi_raw = pi_raw['value']
    pi_cross = bool(isinstance(pi_raw, dict) and pi_raw.get('cross'))

    # ── Pass 2: regime weights ───────────────────────────────────────────────
    sm = build_slider_map_v2(metrics)
    sm['cipherb'] = adaptive_cipherb_blend(metrics, regime)
    oc_avg   = weighted_score(oc_w,   sm)
    tech_avg = weighted_score(tech_w, sm)

    # ── TiZ (bottom regime only) ─────────────────────────────────────────────
    tiz_score, tiz_days = compute_tiz() if regime == 'bottom' else (None, 0)
    tiz_maturity = round(tiz_days / _TIZ_CALIBRATION, 3) if tiz_days > 0 else None

    # ── Final blend ──────────────────────────────────────────────────────────
    def safe(v): return v if v is not None else 0

    if regime == 'bottom' and tiz_score is not None:
        final = round(0.40 * safe(oc_avg) + 0.40 * safe(tech_avg) + 0.20 * tiz_score)
    else:
        parts = [x for x in [oc_avg, tech_avg] if x is not None]
        final = round(sum(parts) / len(parts)) if parts else None

    # ── Coherence dampening (regime-aware) ──────────────────────────────────
    # Pulls final_score toward 50 when OC metrics disagree.
    # Floor varies by regime:
    #   bottom → 0.60 floor  : early crashes have lagging metrics; don't over-suppress genuine bottoms
    #   top    → 0.30 floor  : strong skepticism when metrics diverge at potential tops
    #   neutral→ 0.45 floor  : balanced default
    if regime == 'bottom':
        coh_floor = 0.60
    elif regime == 'top':
        coh_floor = 0.30
    else:
        coh_floor = 0.45
    coh_factor = round(coh_floor + (1.0 - coh_floor) * oc_coherence, 3)
    if final is not None:
        final = round(50 + (final - 50) * coh_factor)

    # ── Pi Cycle cross override (applied after coherence dampening) ──────────
    if pi_cross and final is not None:
        final = max(final, 85)

    def blend(oc, tech, oc_w_ratio):
        if oc is None and tech is None: return None
        if oc is None:   return round(tech)
        if tech is None: return round(oc)
        return round(oc * oc_w_ratio + tech * (1 - oc_w_ratio))

    w_adj, d_adj = _cipherb_adjusted_scores(metrics)
    is_crash = (w_adj is not None and d_adj is not None
                and (w_adj - d_adj) > _CRASH_DIV_THRESHOLD
                and d_adj < _CRASH_DAILY_MAX)
    cb_ratio = 1.0 if regime == 'top' else 0.40 if is_crash else 0.80

    # ── Wave Resonance (rolling-percentile coherence formula) ────────────────
    # Provides fresh raw values so today's live data supplements the unified file.
    def _raw(key):
        obj = metrics.get(key)
        if obj is None: return None
        if isinstance(obj, dict) and 'value' in obj:
            v = obj['value']
            return v.get('value') if isinstance(v, dict) else v
        return obj

    pi_raw_obj = metrics.get('pi_cycle') or {}
    if isinstance(pi_raw_obj, dict) and 'value' in pi_raw_obj:
        pi_raw_obj = pi_raw_obj['value']
    pi_gap_live = (pi_raw_obj.get('gap_pct') if isinstance(pi_raw_obj, dict) else None)

    _live = {
        'nupl':           _raw('nupl'),
        'mvrv':           _raw('mvrv'),
        'puell':          _raw('puell_multiple'),
        'cvdd_ratio':     _raw('cvdd_ratio'),
        'mayer_multiple': _raw('mayer_multiple'),
        'pi_gap_pct':     pi_gap_live,
    }
    wr = compute_wave_resonance(
        date=None,
        raw_overrides={k: v for k, v in _live.items() if v is not None},
    )

    signal = orchestrate(
        v2_score=final,
        v2_oc_coherence=oc_coherence,
        wr_score=wr.get('score'),
        wr_coherence=wr.get('coherence'),
        tiz_maturity=tiz_maturity,
    )

    return {
        'onchain_avg':      oc_avg,
        'tech_avg':         tech_avg,
        'onchain_score':    blend(oc_avg, tech_avg, 0.80),
        'tech_score':       blend(oc_avg, tech_avg, 0.20),
        'final_score':      final,
        'adaptive':         dict(ADAPTIVE_DEBUG),
        'regime':           regime,
        'tiz_score':        tiz_score,
        'tiz_days':         tiz_days,
        'oc_weights':       list(oc_w.keys()),
        'cipherb_blend_w':  cb_ratio,
        'cipherb_crash':    is_crash,
        'oc_coherence':     oc_coherence,
        'coh_factor':       coh_factor,
        'pi_cross':         pi_cross,
        'wave_resonance':   wr,
        'signal':           signal,
    }


__all__ = ['compute_scores_v2', 'map_puell', 'adaptive_cipherb_blend',
           'score_processor_v2', 'build_wave_vector', 'phase_signals']

# ── Score Processor v2 — distance-based (zero free parameters) ───────────────
# wave_vector = [score × 11] + [delta × 11]
# score = d_bottom / (d_top + d_bottom) × 100
# Centroids precomputed by tools/train_adaptive_weights.py → data/adaptive_weights.json

import json as _json, math as _math, os as _os

_V2_PARAMS_PATH = _os.path.join('data', 'adaptive_weights.json')
_v2_cache = {}

_METRIC_LOOKBACK = {
    'nupl': 30, 'mvrv_z_score': 30, 'rhodl_ratio': 30,
    'cvdd_ratio': 30, 'mayer_multiple': 30,
    'asopr': 14, 'etf_flows': 14,
    'cipherb_weekly': 14, 'cipherb_daily': 7, 'fear_greed': 7,
    'm2_yoy': 60, 'yield_curve_spread': 60,
}

def _load_v2_centroids():
    if _v2_cache:
        return _v2_cache
    try:
        p = _json.load(open(_V2_PARAMS_PATH, encoding='utf-8'))
        _v2_cache['top']         = p['top_centroid']
        _v2_cache['bottom']      = p['bottom_centroid']
        _v2_cache['order']       = p['metric_order']
        _v2_cache['calibration'] = p.get('calibration', {})
        _v2_cache['stds']        = p.get('metric_stds')   # diagonal Mahalanobis σ
    except Exception:
        pass
    return _v2_cache


def _mahalanobis_diag(v1, v2, stds):
    """Standardised Euclidean (diagonal Mahalanobis): each dim / σ.

    High-volatility tech metrics (CipherB, FG) have large σ → less influence.
    Stable on-chain metrics (NUPL, MVRV) have small σ → more influence.
    Falls back to plain Euclidean when stds is None.
    """
    total, n = 0.0, 0
    if stds:
        for a, b, s in zip(v1, v2, stds):
            if a is not None and b is not None and s and s > 0:
                total += ((a - b) / s) ** 2
                n += 1
    else:
        for a, b in zip(v1, v2):
            if a is not None and b is not None:
                total += (a - b) ** 2
                n += 1
    return _math.sqrt(total / n) if n > 0 else None


def build_wave_vector(current_scores: dict, prev_scores: dict) -> list:
    """22-dim vector: [scores × 11, deltas × 11] ordered by _METRIC_LOOKBACK."""
    order = list(_METRIC_LOOKBACK)
    vec = [current_scores.get(m) for m in order]
    for m in order:
        s, p = current_scores.get(m), prev_scores.get(m)
        vec.append(s - p if s is not None and p is not None else None)
    return vec


def phase_signals(current_scores: dict, prev_scores: dict) -> dict:
    """
    Dual proximity signals — where in the cycle are we?

    top_signal:  0-100%  how close metrics are to confirmed TOP territory
                 100% = identical to Nov 2021 ATH (most extreme top in training)
    bot_signal:  0-100%  how close metrics are to confirmed BOTTOM territory
                 100% = identical to Jun 2022 capitulation (most extreme bottom)

    phase: 'TOP' | 'BOTTOM' | 'BULL' | 'BEAR' | 'NEUTRAL'
      TOP/BOTTOM   — one signal > 50% AND clearly dominant
      BULL/BEAR    — neither > 50%, but one wins by > 10 points (direction signal)
      NEUTRAL      — signals within 10 points of each other (genuine uncertainty)

    Key property: top_sig + bot_sig do NOT sum to 100.
    Both can be low (transition), never both high (geometry prevents it).
    """
    params = _load_v2_centroids()
    if not params or 'top' not in params or 'calibration' not in params:
        return {'top_signal': None, 'bot_signal': None, 'phase': 'UNKNOWN'}

    cal   = params['calibration']
    top_c = params['top']
    bot_c = params['bottom']
    stds  = params.get('stds')
    vec   = build_wave_vector(current_scores, prev_scores)
    d_top = _mahalanobis_diag(vec, top_c, stds)
    d_bot = _mahalanobis_diag(vec, bot_c, stds)
    if d_top is None or d_bot is None:
        return {'top_signal': None, 'bot_signal': None, 'phase': 'UNKNOWN'}

    top_range = cal['d_top_max'] - cal['d_top_min']
    bot_range = cal['d_bot_max'] - cal['d_bot_min']
    tsig = max(0, min(100, round((cal['d_top_max'] - d_top) / top_range * 100)))
    bsig = max(0, min(100, round((cal['d_bot_max'] - d_bot) / bot_range * 100)))

    diff = tsig - bsig
    if   tsig > 50 and tsig > bsig: phase = 'TOP'
    elif bsig > 50 and bsig > tsig: phase = 'BOTTOM'
    elif diff >  10:                 phase = 'BULL'
    elif diff < -10:                 phase = 'BEAR'
    else:                            phase = 'NEUTRAL'

    return {'top_signal': tsig, 'bot_signal': bsig, 'phase': phase}


def score_processor_v2(current_scores: dict, prev_scores: dict):
    """
    Distance-based risk score — zero free parameters.

    current_scores: {metric: score_0_100} — from score_from_raw() at today
    prev_scores:    {metric: score_0_100} — each metric from its natural lookback ago
                    (7d for cipherb/fear_greed, 14d for asopr/etf, 30d for OC, 60d for macro)

    Returns 0-100 (100 = closest to confirmed cycle top).
    """
    params = _load_v2_centroids()
    if not params:
        return None
    stds    = params.get('stds')
    vec     = build_wave_vector(current_scores, prev_scores)
    d_top   = _mahalanobis_diag(vec, params['top'],    stds)
    d_bot   = _mahalanobis_diag(vec, params['bottom'], stds)
    if d_top is None or d_bot is None:
        return None
    denom = d_top + d_bot
    return round(d_bot / denom * 100) if denom > 0 else 50
