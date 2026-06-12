"""
Signal Orchestrator — hybrid meta-score from V2 + Wave Resonance.

Algorithm:
  1. Trust scores
       wr_trust = wave_resonance coherence   (how synchronised are on-chain oscillators)
       v2_trust = oc_coherence               (how synchronised are V2 on-chain sliders)
  2. Zone agreement
       Each track classified as bottom / neutral / top.
       At bottoms WR < V2 is expected (on-chain leads tech); not penalised.
       At tops   WR > V2 is expected (on-chain leads tech).
       Contradiction = same track disagrees with the direction.
  3. Dynamic blend
       weights proportional to trust scores → meta_score
  4. Conviction
       (avg_trust) × agreement_factor
         same zone + expected direction → factor 1.0
         same zone + unexpected direction → 0.8
         different zones, plausible → 0.5
         contradiction → 0.25
  5. TiZ temporal gate (bottom zone, when tiz_maturity provided)
       conviction scaled by (0.40 + 0.60 × tiz_maturity)
       tiz_maturity < 0.25 → EARLY_ZONE (too early to call bottom)
       tiz_maturity < 0.50 → cap CONFIRMED_BOTTOM → PROBABLE_BOTTOM
  6. Flag (after TiZ scaling)
       CONFIRMED_BOTTOM   meta ≤ 25 AND conviction ≥ 0.60
       PROBABLE_BOTTOM    meta ≤ 38 AND conviction ≥ 0.40
       EARLY_ZONE         bottom zone but TiZ maturity < 25%
       CONFIRMED_TOP      meta ≥ 78 AND conviction ≥ 0.60
       PROBABLE_TOP       meta ≥ 65 AND conviction ≥ 0.40
       UNCERTAIN          conviction < 0.30
       NEUTRAL            otherwise
"""

_BOTTOM_T = 38   # meta threshold for bottom zone
_TOP_T    = 65   # meta threshold for top zone
_BOTTOM_C = 25   # confirmed bottom threshold
_TOP_C    = 78   # confirmed top threshold

# ML-validated anchor (tools/blend_optimizer.py): WR alone minimises MSE on
# 6-month forward returns (r=0.104 vs r=0.082 for V2 alone).
# 80/20 ratio encoded as a 4× trust boost for WR.
# At equal coherence → exactly 80% WR / 20% V2.
# Coherence can still push weights ±10-15% around this anchor.
_ML_WR_BOOST = 4.0


def _zone(score):
    if score is None: return 'unknown'
    if score < 40: return 'bottom'
    if score > 65: return 'top'
    return 'neutral'


def orchestrate(v2_score, v2_oc_coherence, wr_score, wr_coherence, tiz_maturity=None):
    """Combine V2 and Wave Resonance into a single meta signal.

    Parameters
    ----------
    v2_score          : int         [0,100] from compute_scores_v2['final_score']
    v2_oc_coherence   : float       [0,1]   from compute_scores_v2['oc_coherence']
    wr_score          : int         [0,100] from wave_resonance['score']
    wr_coherence      : float       [0,1]   from wave_resonance['coherence']
    tiz_maturity      : float|None  [0,1]   days_in_zone / calibration_days (bottom only)

    Returns dict:
      meta_score    — int [0,100]  trust-weighted blend
      zone          — 'bottom' | 'neutral' | 'top'
      conviction    — float [0,1]  (TiZ-scaled in bottom zone)
      flag          — CONFIRMED_BOTTOM | PROBABLE_BOTTOM | EARLY_ZONE | NEUTRAL |
                      PROBABLE_TOP | CONFIRMED_TOP | UNCERTAIN
      trust         — {'v2': float, 'wr': float}  normalised weights used
      agreement     — 'confirmed' | 'directional' | 'partial' | 'divergent'
      tiz_maturity  — float|None  echo of input
    """
    # ── Trust weights (ML-anchored coherence blend) ──────────────────────────
    v2_t = max(0.01, v2_oc_coherence or 0.0)
    wr_t = max(0.01, wr_coherence    or 0.0) * _ML_WR_BOOST

    if wr_score is None:
        # WR unavailable — fall back to V2 alone
        v2_conv = round(v2_t, 3)
        if tiz_maturity is not None and v2_score is not None and v2_score <= _BOTTOM_T:
            v2_conv = round(v2_t * (0.40 + 0.60 * tiz_maturity), 3)
        flag = _flag(v2_score, v2_conv)
        flag = _tiz_gate(flag, tiz_maturity, v2_score)
        return {
            'meta_score':   v2_score,
            'zone':         _zone(v2_score),
            'conviction':   v2_conv,
            'flag':         flag,
            'trust':        {'v2': 1.0, 'wr': 0.0},
            'agreement':    'wr_unavailable',
            'tiz_maturity': tiz_maturity,
        }

    total = v2_t + wr_t
    w_v2  = v2_t / total
    w_wr  = wr_t / total

    meta = round(w_v2 * v2_score + w_wr * wr_score)

    # ── Zone agreement ───────────────────────────────────────────────────────
    z_v2 = _zone(v2_score)
    z_wr = _zone(wr_score)
    # avg_trust for conviction uses un-boosted coherences so result stays [0,1]
    v2_raw = max(0.01, v2_oc_coherence or 0.0)
    wr_raw = max(0.01, wr_coherence    or 0.0)
    avg_trust = (v2_raw + wr_raw) / 2

    if z_v2 == z_wr:
        # Both say the same zone
        # At bottom: WR ≤ V2 is expected (on-chain leads). WR > V2 is unusual.
        # At top:    WR ≥ V2 is expected. WR < V2 is unusual.
        _TOL = 5  # ignore direction within ±5 pts (numerical noise)
        if z_v2 == 'bottom':
            direction_ok = (wr_score <= v2_score + _TOL)
        elif z_v2 == 'top':
            direction_ok = (wr_score >= v2_score - _TOL)
        else:
            direction_ok = True
        factor    = 1.0 if direction_ok else 0.8
        agreement = 'confirmed' if direction_ok else 'same_zone_reversed'
    elif z_v2 != 'neutral' and z_wr == 'neutral':
        # V2 has a view, WR is ambivalent — plausible (tech leads on-chain at turning point)
        factor    = 0.5
        agreement = 'partial_v2_leads'
    elif z_v2 == 'neutral' and z_wr != 'neutral':
        # WR has a view, V2 is neutral — on-chain signal not yet confirmed by macro
        factor    = 0.5
        agreement = 'partial_wr_leads'
    else:
        # Genuine contradiction (one says bottom, other says top)
        factor    = 0.25
        agreement = 'divergent'

    conviction = round(avg_trust * factor, 3)

    # TiZ temporal scaling: in bottom zone, conviction shrinks with immaturity.
    # Day 0: ×0.40 — fresh entry, high uncertainty
    # Day 200 (calibration): ×1.00 — mature zone, full weight
    if tiz_maturity is not None and meta <= _BOTTOM_T:
        conviction = round(conviction * (0.40 + 0.60 * tiz_maturity), 3)

    flag = _flag(meta, conviction)
    flag = _tiz_gate(flag, tiz_maturity, meta)

    return {
        'meta_score':   meta,
        'zone':         _zone(meta),
        'conviction':   conviction,
        'flag':         flag,
        'trust':        {'v2': round(w_v2, 3), 'wr': round(w_wr, 3)},
        'agreement':    agreement,
        'tiz_maturity': tiz_maturity,
    }


def _tiz_gate(flag, tiz_maturity, meta):
    """Hard cap on bottom flags based on temporal maturity.

    < 25% maturity (~50 days): too early to call any bottom → EARLY_ZONE
    < 50% maturity (~100 days): cannot confirm yet → cap at PROBABLE_BOTTOM
    ≥ 50%: no override (conviction threshold applies normally)
    Only applied when meta is in bottom zone and tiz_maturity is provided.
    """
    if tiz_maturity is None or meta is None or meta > _BOTTOM_T:
        return flag
    if tiz_maturity < 0.25:
        if flag in ('CONFIRMED_BOTTOM', 'PROBABLE_BOTTOM'):
            return 'EARLY_ZONE'
    elif tiz_maturity < 0.50:
        if flag == 'CONFIRMED_BOTTOM':
            return 'PROBABLE_BOTTOM'
    return flag


def _flag(meta, conviction):
    if meta <= _BOTTOM_C and conviction >= 0.60: return 'CONFIRMED_BOTTOM'
    if meta <= _BOTTOM_T and conviction >= 0.40: return 'PROBABLE_BOTTOM'
    if meta >= _TOP_C    and conviction >= 0.60: return 'CONFIRMED_TOP'
    if meta >= _TOP_T    and conviction >= 0.40: return 'PROBABLE_TOP'
    if conviction < 0.30:                        return 'UNCERTAIN'
    return 'NEUTRAL'


__all__ = ['orchestrate']
