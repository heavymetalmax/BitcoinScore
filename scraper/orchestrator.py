"""
Signal Orchestrator — hybrid meta-score from V2 + Wave Resonance.
Stage 2 conviction model loaded lazily from data/v3_phase_model.pkl.

Algorithm:
  1. Trust scores
       base_trust = v2_oc_coherence  (how synchronised are V2 on-chain sliders)
  2. Zone agreement
       Each track classified as bottom / neutral / top.
       WR zone compared with V2 zone to derive wr_agreement_factor.
  3. meta = fisher_score (незмінно); conviction = v2_oc_coherence × wr_agreement_factor
  4. TiZ temporal gate (bottom zone, when tiz_maturity provided)
       conviction scaled by (0.40 + 0.60 × tiz_maturity)
       tiz_maturity < 0.25 → EARLY_ZONE (too early to call bottom)
       tiz_maturity < 0.50 → cap CONFIRMED_BOTTOM → PROBABLE_BOTTOM
  5. Flag (after TiZ scaling)
       CONFIRMED_BOTTOM   meta ≤ 25 AND conviction ≥ 0.60
       PROBABLE_BOTTOM    meta ≤ 38 AND conviction ≥ 0.40
       EARLY_ZONE         bottom zone but TiZ maturity < 25%
       CONFIRMED_TOP      meta ≥ 78 AND conviction ≥ 0.60
       PROBABLE_TOP       meta ≥ 65 AND conviction ≥ 0.40
       UNCERTAIN          conviction < 0.30
       NEUTRAL            otherwise
"""

import math as _math
import os as _os
import pickle as _pickle

_BOTTOM_T = 38   # meta threshold for bottom zone
_TOP_T    = 65   # meta threshold for top zone
_BOTTOM_C = 25   # confirmed bottom threshold
_TOP_C    = 78   # confirmed top threshold


def _zone(score):
    if score is None: return 'unknown'
    if score < 40: return 'bottom'
    if score > 65: return 'top'
    return 'neutral'


def _zone_eff(score, top_t=65, bot_t=38):
    if score is None: return 'unknown'
    if score < bot_t: return 'bottom'
    if score > top_t: return 'top'
    return 'neutral'


_S2_CACHE = {}


def _load_stage2():
    """Lazy-load Stage 2 conviction weights from pkl."""
    if _S2_CACHE:
        return _S2_CACHE
    try:
        pkl_path = _os.path.join(_os.path.dirname(__file__), '..', 'data', 'v3_phase_model.pkl')
        with open(pkl_path, 'rb') as f:
            md = _pickle.load(f)
        _S2_CACHE.update(md.get('stage2', {}))
    except Exception:
        pass
    return _S2_CACHE


def _sig(x):
    return 1.0 / (1.0 + _math.exp(-max(-50.0, min(50.0, x))))


def _conviction_bottom_stage2(w_bot, tiz_maturity, oc_coherence):
    """Stage 2 data-driven conviction for BOTTOM zone."""
    s2 = _load_stage2().get('bottom')
    if s2 is None:
        return round(w_bot * 0.70 + tiz_maturity * 0.15 + oc_coherence * 0.15, 3)
    c = s2['coef']
    logit = c[0] * w_bot + c[1] * tiz_maturity + c[2] * oc_coherence + s2['intercept']
    return round(min(0.95, _sig(logit)), 3)


def _conviction_top_stage2(w_top, oc_coherence):
    """Stage 2 data-driven conviction for TOP zone."""
    s2 = _load_stage2().get('top')
    if s2 is None:
        return round(w_top * 0.80 + oc_coherence * 0.20, 3)
    c = s2['coef']
    logit = c[0] * w_top + c[1] * oc_coherence + s2['intercept']
    return round(min(0.95, _sig(logit)), 3)


def orchestrate(v2_score, v2_oc_coherence, wr_score, wr_coherence, tiz_maturity=None,
                top_signal=None, bot_signal=None, is_v3=False, btc_price=None,
                target_date=None, scores_history=None):
    """Combine V2/V3 and Wave Resonance into a single meta signal.

    Parameters
    ----------
    v2_score          : int         [0,100] from compute_scores_v2['final_score'] (or v3 final_score)
    v2_oc_coherence   : float       [0,1]   from compute_scores_v2['oc_coherence'] (or v3 oc_coherence)
    wr_score          : int         [0,100] from wave_resonance['score']
    wr_coherence      : float       [0,1]   from wave_resonance['coherence']
    tiz_maturity      : float|None  [0,1]   days_in_zone / calibration_days (bottom only)
    top_signal        : float|None  [0,100] geometric top proximity from phase_signals
    bot_signal        : float|None  [0,100] geometric bottom proximity from phase_signals
    is_v3             : bool        True if orchestrating V3 score instead of V2
    btc_price         : float|None  Current BTC price for divergence checks
    target_date       : datetime.date|str|None Target date for causal filtering
    scores_history    : list|None   Preloaded scores history list

    Returns dict:
      meta_score    — int [0,100]  trust-weighted blend
      zone          — 'bottom' | 'neutral' | 'top'
      conviction    — float [0,1]  (TiZ-scaled in bottom zone, geo-boosted)
      flag          — CONFIRMED_BOTTOM | PROBABLE_BOTTOM | EARLY_ZONE | NEUTRAL |
                      PROBABLE_TOP | CONFIRMED_TOP | UNCERTAIN
      trust         — {'v2' or 'v3': float, 'wr': float}  normalised weights used
      agreement     — 'confirmed' | 'directional' | 'partial' | 'divergent'
      tiz_maturity  — float|None  echo of input
      geo_signal    — {'top': float|None, 'bot': float|None}  echo of inputs
      bear_div      — bool        True if bearish divergence detected
    """
    # 1. Base trust (V2/V3 on-chain coherence)
    base_trust = max(0.01, v2_oc_coherence or 0.0)

    # Helper zone membership functions (0-100 scale to 0-1)
    def _mu_top(s):
        return max(0.0, min(1.0, (s - 50.0) / 30.0))
        
    def _mu_bot(s):
        return max(0.0, min(1.0, (50.0 - s) / 25.0))

    # 2. Continuous Geometric Signal Boost
    # Signal above 50% starts to continuously boost the zone membership
    mu_top_sig = max(0.0, min(1.0, (float(top_signal or 0.0) - 50.0) / 30.0)) if top_signal is not None else 0.0
    mu_bot_sig = max(0.0, min(1.0, (float(bot_signal or 0.0) - 50.0) / 30.0)) if bot_signal is not None else 0.0

    mu_top_eff = max(_mu_top(v2_score), mu_top_sig) if v2_score is not None else 0.0
    mu_bot_eff = max(_mu_bot(v2_score), mu_bot_sig) if v2_score is not None else 0.0

    # 3. Continuous Agreement Factor
    # Alignment via dot product of directional components D_S and D_WR
    D_S = mu_top_eff - mu_bot_eff
    
    if wr_score is not None:
        mu_top_wr = _mu_top(wr_score)
        mu_bot_wr = _mu_bot(wr_score)
        D_WR = mu_top_wr - mu_bot_wr
        alignment = D_S * D_WR
        agreement_factor = 0.65 + 0.35 * alignment
        agreement = 'confirmed' if alignment > 0.60 else 'directional' if alignment > 0.0 else 'divergent' if alignment < -0.10 else 'partial'
    else:
        agreement_factor = 0.70
        agreement = 'wr_unavailable'

    # Stage 2: data-driven conviction from ML-trained weights
    w_bot_proxy = (float(bot_signal) / 100.0) if bot_signal is not None else 0.0
    w_top_proxy = (float(top_signal) / 100.0) if top_signal is not None else 0.0

    if mu_bot_eff > mu_top_eff:
        conviction = _conviction_bottom_stage2(w_bot_proxy, tiz_maturity or 0.0, v2_oc_coherence or 0.5)
    elif mu_top_eff > mu_bot_eff:
        conviction = _conviction_top_stage2(w_top_proxy, v2_oc_coherence or 0.5)
    else:
        conviction = round(base_trust * agreement_factor, 3)

    # 4. Continuous Conviction Pull
    # Bottom pull scales with TiZ maturity (early zone = soft pull, mature = full pull).
    # Top pull uses fixed coefficient (no TiZ equivalent for tops).
    meta = v2_score if v2_score is not None else 50
    if conviction > 0.40:
        pull_factor = (conviction - 0.40) / 0.60
        bot_coef = 0.60 * (tiz_maturity if tiz_maturity is not None else 1.0)
        pull_top = (85.0 - meta) * mu_top_eff * pull_factor * 0.60
        pull_bot = (15.0 - meta) * mu_bot_eff * pull_factor * bot_coef
        meta = round(meta + pull_top + pull_bot)

    # 5. Causal Bearish & Bullish Divergence Detection
    bear_div = False
    bull_div = False
    import datetime
    import json
    
    # Resolve target date
    if target_date is None:
        td = datetime.date.today()
    elif isinstance(target_date, str):
        td = datetime.date.fromisoformat(target_date)
    else:
        td = target_date
        
    td_str = td.isoformat()
    
    # Load history if not provided
    hist = scores_history
    if hist is None:
        try:
            path = _os.path.join(_os.path.dirname(__file__), '..', 'data', 'history', 'scores.json')
            if _os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    hist = json.load(f)
        except Exception:
            pass
            
    if hist:
        # Filter causally (strictly before target_date)
        valid_hist = [x for x in hist if x.get('date') and x['date'] < td_str]
        
        # Calculate dynamic lookback days based on annualized daily log returns volatility over trailing 365 days
        lookback_days = 45
        t365_cutoff = (td - datetime.timedelta(days=365)).isoformat()
        hist_365 = [x for x in valid_hist if x['date'] >= t365_cutoff]
        p_vals = [x.get('btc_price') for x in hist_365 if x.get('btc_price') is not None]
        if len(p_vals) >= 30:
            import math
            returns = []
            for idx in range(1, len(p_vals)):
                if p_vals[idx-1] > 0:
                    returns.append(math.log(p_vals[idx] / p_vals[idx-1]))
            if len(returns) >= 29:
                mean_r = sum(returns) / len(returns)
                var_r = sum((r - mean_r)**2 for r in returns) / (len(returns) - 1)
                std_r = math.sqrt(var_r)
                vol_ann = std_r * math.sqrt(365) # annualized
                
                # Map volatility [0.30, 0.90] to lookback [45, 30]
                if vol_ann <= 0.30:
                    lookback_days = 45
                elif vol_ann >= 0.90:
                    lookback_days = 30
                else:
                    pct = (vol_ann - 0.30) / 0.60
                    lookback_days = int(round(45 - pct * (45 - 30)))
                    
        # Look back dynamic days
        t_cutoff = (td - datetime.timedelta(days=lookback_days)).isoformat()
        trailing = [x for x in valid_hist if x['date'] >= t_cutoff]
        
        if len(trailing) >= 5 and btc_price is not None:
            prices_t = [x.get('btc_price') for x in trailing if x.get('btc_price') is not None]
            scores_t = [x.get('final_score') for x in trailing if x.get('final_score') is not None]
            
            if prices_t and scores_t:
                max_price_t = max(prices_t)
                min_price_t = min(prices_t)
                max_score_t = max(scores_t)
                min_score_t = min(scores_t)
                
                # Bearish Divergence conditions:
                # 1. Price is near or above dynamic high (within 3%)
                price_near_high = (btc_price >= 0.97 * max_price_t)
                # 2. Risk score has decayed significantly from its peak in that period (dropped by >= 8 points)
                score_decayed = (meta <= max_score_t - 8)
                # 3. Current score is in the distribution/neutral-high zone (prevents false alerts at bottoms)
                is_elevated = (meta >= 58)
                
                if price_near_high and score_decayed and is_elevated:
                    bear_div = True
                    # Override meta score to stay elevated (prevent fake cooldown)
                    meta = max(meta, 72)

                # Bullish Divergence conditions:
                # 1. Price is near or below dynamic low (within 3%)
                price_near_low = (btc_price <= 1.03 * min_price_t)
                # 2. Risk score has risen significantly from its minimum in that period (risen by >= 8 points)
                score_risen = (meta >= min_score_t + 8)
                # 3. Current score is in the accumulation/neutral-low zone (prevents false alerts at tops)
                is_depressed = (meta <= 45)
                
                if price_near_low and score_risen and is_depressed:
                    bull_div = True
                    # Override meta score to stay low (prevent fake early risk run-up)
                    meta = min(meta, 25)

    # For zone/flag outputs, we can compute them based on adaptive thresholds
    # to maintain full UI compatibility
    eff_top_t = max(55.0, _TOP_T - max(0.0, float(top_signal or 0.0) - 70.0) * 0.35) if top_signal is not None else _TOP_T
    eff_bot_t = min(45.0, _BOTTOM_T + max(0.0, float(bot_signal or 0.0) - 70.0) * 0.35) if bot_signal is not None else _BOTTOM_T

    flag = _flag(meta, conviction, eff_top_t, eff_bot_t)
    if bear_div:
        # Elevate flag to reflect the divergence risk
        if flag not in ('CONFIRMED_TOP', 'PROBABLE_TOP'):
            flag = 'PROBABLE_TOP'
    elif bull_div:
        # Depress flag to reflect the divergence opportunity
        if flag not in ('CONFIRMED_BOTTOM', 'PROBABLE_BOTTOM'):
            flag = 'PROBABLE_BOTTOM'

    flag = _tiz_gate(flag, tiz_maturity, meta)

    return {
        'meta_score':   meta,
        'zone':         _zone_eff(meta, eff_top_t, eff_bot_t),
        'conviction':   conviction,
        'flag':         flag,
        'trust':        {'v3' if is_v3 else 'v2': round(v2_oc_coherence or 0, 3), 'wr': round(wr_coherence or 0, 3) if wr_score is not None else 0},
        'agreement':    agreement,
        'tiz_maturity': tiz_maturity,
        'geo_signal':   {'top': top_signal, 'bot': bot_signal},
        'bear_div':     bear_div,
        'bull_div':     bull_div,
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


def _flag(meta, conviction, eff_top_t=None, eff_bot_t=None):
    _top_t = eff_top_t if eff_top_t is not None else _TOP_T
    _bot_t = eff_bot_t if eff_bot_t is not None else _BOTTOM_T
    if meta <= _BOTTOM_C and conviction >= 0.60: return 'CONFIRMED_BOTTOM'
    if meta <= _bot_t    and conviction >= 0.40: return 'PROBABLE_BOTTOM'
    if meta >= _TOP_C    and conviction >= 0.60: return 'CONFIRMED_TOP'
    if meta >= _top_t    and conviction >= 0.40: return 'PROBABLE_TOP'
    if conviction < 0.30:                        return 'UNCERTAIN'
    return 'NEUTRAL'


__all__ = ['orchestrate']
