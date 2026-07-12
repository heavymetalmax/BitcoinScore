"""Scoring V3.1 Engine — Continuous Normalization and Mixing.

Architecture:
  1. Normalize raw metrics using causal point-in-time normalizer.
  2. Compute Pi Cycle Gap and map it to a 0-100 risk score.
  3. Fetch lookback scores and compute cycle phase proximity (TOP/BOTTOM/NEUTRAL etc.)
     via Mahalanobis distance to centroids.
  4. Derive continuous state mixture weights: w_top, w_bottom, w_neutral.
  5. Evaluate continuous utility coefficients U_i in [0.1, 1.0] for each metric.
  6. Dynamically mix normalized scores using utility coefficients as weights for group averages.
  7. Apply coherence dampening and Time-in-Zone scaling.
"""

import datetime
import os
import json
import pickle
import numpy as np
from scraper.normalizer import normalize_metric
from scraper.utility_evaluator import evaluate_all_utilities_continuous
from scraper.scoring_v2 import phase_signals, _METRIC_LOOKBACK
from scraper.scoring import _oc_coherence
from scraper.tiz import adaptive_calibration as _tiz_adaptive_cal

# Metric groups for z-weighting
OC_GROUP = {'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'asopr', 'puell', 'lth_supply'}
TECH_GROUP = {'cipherb', 'mayer_multiple', 'fear_greed', 'etf_flows', 'yield_curve_spread', 'm2_yoy', 'pi_gap', 'funding_rate'}
# dxy removed from TECH_GROUP — it acts as a Macro Modifier in scoring_pipeline.py instead

# Bottom and top thresholds for regime mapping (aligned with V2)
BOTTOM_THRESHOLD = 40
TOP_THRESHOLD = 65


def extract_pi_gap(v):
    """Robustly extract the Pi Cycle gap percentage from raw formats."""
    if v is None:
        return None
    while isinstance(v, dict):
        if 'gap_pct' in v:
            return v['gap_pct']
        v = v.get('value')
    if isinstance(v, (int, float)):
        return v
    return None


def map_pi_cycle_gap(v):
    """Map Pi Cycle Gap percentage to a 0-100 risk score.

    0% or cross -> 100 risk (immediate top)
    70% or more -> 0 risk (extremely safe)
    Historical range: -0.3% to 71.3%; 30% cap compressed 80% of history to 0.
    """
    gap = extract_pi_gap(v)
    if gap is None:
        return None
    gap = max(0.0, min(70.0, float(gap)))
    return round(((70.0 - gap) / 70.0) * 100)


def compute_tiz_causal_v3(scores_history, target_date, threshold=40, window=180):
    """Causal Time-in-Zone calculation for backtesting or live runs.

    Zone detection uses phase signal when available (preferred), falls back to
    score <= threshold for records without phase data (older entries).
    Returns (tiz_score, days_in_zone, calibration).
    """
    if not scores_history:
        return None, 0, 200

    target_date_str = target_date.isoformat()
    cutoff_date = (target_date - datetime.timedelta(days=window)).isoformat()

    history = sorted([h for h in scores_history if h[0] <= target_date_str], key=lambda x: x[0])
    if not history:
        return None, 0, 200

    latest = history[-1]
    latest_date = latest[0]
    latest_score = latest[1]
    latest_phase = latest[2] if len(latest) > 2 else None
    latest_dt = datetime.date.fromisoformat(latest_date)

    # Most recent record must be in zone; use phase if available, else score threshold
    if (target_date - latest_dt).days > 7:
        return None, 0, 200
    if latest_phase is not None:
        if latest_phase != 'BOTTOM':
            return None, 0, 200
    elif latest_score is None or latest_score > threshold:
        return None, 0, 200

    def _in_zone(record):
        d, s = record[0], record[1]
        phase = record[2] if len(record) > 2 else None
        if not (cutoff_date <= d <= target_date_str):
            return False
        if phase is not None:
            return phase == 'BOTTOM'
        return s is not None and s <= threshold

    in_zone = [r for r in history if _in_zone(r)]

    if not in_zone:
        return None, 0, 200

    min_score = min(r[1] for r in in_zone if r[1] is not None)
    calibration = _tiz_adaptive_cal(min_score)
    days_in_zone = len(in_zone)

    progress = min(1.0, days_in_zone / calibration)
    tiz_score = round(38 + (5 - 38) * progress)
    return tiz_score, days_in_zone, calibration


def compute_scores_v3(raw_metrics, target_date=None, prev_scores=None, scores_history=None):
    """Compute composite score using dynamic continuous weights.

    Parameters:
    -----------
    raw_metrics : dict
        Raw values from live scraper or historical data.
    target_date : datetime.date or str, optional
        Target date for causal normalization/utility evaluation.
    prev_scores : dict, optional
        Pre-computed lookback scores. If None, loaded causally.
    scores_history : list of tuples [(date_str, score)], optional
        Historical final scores for TiZ calculation.
    """
    if isinstance(target_date, str):
        target_date = datetime.date.fromisoformat(target_date)
    elif target_date is None:
        target_date = datetime.date.today()

    # 1. Normalize all metrics to 0-100 scale
    normalized = {}
    
    key_mapping = {
        'nupl': 'nupl',
        'mvrv': 'mvrv_z_score',
        'rhodl_ratio': 'rhodl_ratio',
        'cvdd_ratio': 'cvdd_ratio',
        'asopr': 'asopr',
        'puell_multiple': 'puell',
        'mayer_multiple': 'mayer_multiple',
        'fear_greed': 'fear_greed',
        'm2_mom': 'm2_yoy',              # data.json key is m2_mom
        'yield_curve': 'yield_curve_spread',  # data.json key is yield_curve
        'etf_flows': 'etf_flows',
        'funding_rate': 'funding_rate',
        'dxy': 'dxy',
        'lth_supply_pct': 'lth_supply',
    }
    
    for raw_k, norm_k in key_mapping.items():
        val = raw_metrics.get(raw_k)
        normalized[norm_k] = normalize_metric(norm_k, val, target_date)

    # 2. Add Pi Cycle Gap metric
    pi_raw = raw_metrics.get('pi_cycle') or raw_metrics.get('pi_cycle_gap_pct') or raw_metrics.get('pi_gap')
    normalized['pi_gap'] = map_pi_cycle_gap(pi_raw)

    # Separate CipherB handling
    cb = raw_metrics.get('cipherb')
    cb_weekly_raw = None
    cb_daily_raw = None
    cipherb_score = None
    
    if isinstance(cb, dict):
        val_dict = cb.get('value') if 'value' in cb else cb
        w = None
        d = None
        if isinstance(val_dict, dict):
            w = val_dict.get('weekly_score')
            d = val_dict.get('daily_score')
        cb_weekly_raw = round(w) if w is not None else None
        cb_daily_raw = round(d) if d is not None else None
        
        if w is not None:
            if val_dict.get('fast_bearish_div'):
                w = min(100.0, w + 12)
            elif val_dict.get('fast_bullish_div'):
                w = max(0.0, w - 12)
            if d is not None:
                if val_dict.get('daily_fast_bearish_div'):
                    d = min(100.0, d + 12)
                elif val_dict.get('daily_fast_bullish_div'):
                    d = max(0.0, d - 12)
                cipherb_score = round(0.8 * w + 0.2 * d)
            else:
                cipherb_score = round(w)
                
    normalized['cipherb'] = cipherb_score
    normalized['cipherb_weekly'] = cb_weekly_raw
    normalized['cipherb_daily'] = cb_daily_raw

    # 3. Fetch lookback scores for phase detection
    if prev_scores is None:
        from scraper.wave_history import build_prev_scores_for_wave
        prev_scores = build_prev_scores_for_wave(target_date, _METRIC_LOOKBACK)

    # 4. Detect cycle phase using ML model or fallback to Mahalanobis centroids
    ml_loaded = False
    w_top, w_bot, w_neutral = 0.0, 0.0, 1.0
    phase = 'NEUTRAL'
    top_signal = 0
    bot_signal = 0

    try:
        model_path = 'data/v3_phase_model.pkl'
        if os.path.exists(model_path):
            _hmm_cls = None
            try:
                from tools.train_v3_hmm_model import HMMPhaseClassifier as _hmm_cls  # noqa
            except ImportError:
                pass

            class _Unpickler(pickle.Unpickler):
                def find_class(self, module, name):
                    if name == 'HMMPhaseClassifier' and _hmm_cls is not None:
                        return _hmm_cls
                    return super().find_class(module, name)

            with open(model_path, 'rb') as f:
                model_data = _Unpickler(f).load()
            pipeline = model_data['pipeline']
            
            # Build 22-dimensional wave vector
            METRIC_ORDER = [
                'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'mayer_multiple',
                'asopr', 'etf_flows', 'cipherb_weekly', 'cipherb_daily', 'fear_greed',
                'm2_yoy',
            ]
            
            vec = []
            # Positions
            for m in METRIC_ORDER:
                vec.append(normalized.get(m))
            # Deltas
            for m in METRIC_ORDER:
                s_now = normalized.get(m)
                s_prev = prev_scores.get(m)
                if s_now is not None and s_prev is not None:
                    vec.append(s_now - s_prev)
                else:
                    vec.append(None)
                    
            row = [v if v is not None else float('nan') for v in vec]
            probs = pipeline.predict_proba(np.array([row], dtype=float))[0]
            
            # Mapping: 0=BOTTOM, 1=NEUTRAL, 2=TOP
            w_bot = float(probs[0])
            w_neutral = float(probs[1])
            w_top = float(probs[2])
            
            top_signal = round(w_top * 100)
            bot_signal = round(w_bot * 100)
            
            if w_top > 0.40 and w_top > w_bot:
                phase = 'TOP'
            elif w_bot > 0.40 and w_bot > w_top:
                phase = 'BOTTOM'
            else:
                phase = 'NEUTRAL'
            ml_loaded = True
    except Exception:
        pass
        
    if not ml_loaded:
        phases = phase_signals(normalized, prev_scores)
        phase = phases.get('phase', 'NEUTRAL')
        top_signal = phases.get('top_signal')
        bot_signal = phases.get('bot_signal')

        w_top = max(0.0, min(100.0, float(top_signal or 0.0))) / 100.0
        w_bot = max(0.0, min(100.0, float(bot_signal or 0.0))) / 100.0
        
        total_w = w_top + w_bot
        if total_w > 1.0:
            w_top /= total_w
            w_bot /= total_w
            w_neutral = 0.0
        else:
            w_neutral = 1.0 - total_w

    # 6. Determine Time-in-Zone (TiZ)
    tiz_score = None
    tiz_days = 0
    tiz_maturity = None
    tiz_calibration = 200
    if phase == 'BOTTOM':
        if scores_history is None:
            scores_path = 'data/history/scores.json'
            if os.path.exists(scores_path):
                try:
                    with open(scores_path, encoding='utf-8') as f:
                        history_data = json.load(f)
                        scores_history = [
                            (r['date'], r.get('final_score'), r.get('phase'), r.get('w_bot'))
                            for r in history_data if r.get('date')
                        ]
                except Exception:
                    pass
        if scores_history:
            tiz_score, tiz_days, tiz_calibration = compute_tiz_causal_v3(
                scores_history, target_date, threshold=BOTTOM_THRESHOLD
            )
            if tiz_days > 0:
                tiz_maturity = round(tiz_days / tiz_calibration, 3)

    # 7. Evaluate dynamic continuous utility coefficients
    utilities = evaluate_all_utilities_continuous(
        normalized, w_top, w_bot, w_neutral, tiz_maturity
    )

    # 8. Dynamic utility-weighted average across a set of metrics
    def weighted_avg(group_keys):
        total_uw = 0.0
        total_us = 0.0
        for k in group_keys:
            s = normalized.get(k)
            u = utilities.get(k, 0.5)
            if s is not None:
                total_us += s * u
                total_uw += u
        return round(total_us / total_uw) if total_uw > 0 else None

    # Informational sub-scores (shown on dashboard, not used for final computation)
    oc_avg = weighted_avg(OC_GROUP)
    tech_avg = weighted_avg(TECH_GROUP)

    # 9. Flat utility-weighted average across ALL metrics — utilities decide the weight,
    #    no hardcoded group split. This is the original V3 intent.
    flat_avg = weighted_avg(OC_GROUP | TECH_GROUP)

    if flat_avg is not None:
        if phase == 'BOTTOM' and tiz_score is not None:
            # In confirmed bottom, blend flat score with TiZ maturity signal
            final = round(0.80 * flat_avg + 0.20 * tiz_score)
        else:
            final = flat_avg
    else:
        final = None

    # 10. Coherence dampening (pulls score toward phase-appropriate target if OC metrics diverge)
    oc_coherence = _oc_coherence(normalized)
    coh_factor = 1.0
    if final is not None:
        # Determine coherence floor and neutral target based on phase/regime
        if phase == 'BOTTOM':
            coh_floor = 0.70
            neutral_target = 26
        elif phase == 'TOP':
            coh_floor = 0.55
            neutral_target = 68
        else:
            coh_floor = 0.45
            neutral_target = 50
        coh_factor = round(coh_floor + (1.0 - coh_floor) * oc_coherence, 3)
        final = round(neutral_target + (final - neutral_target) * coh_factor)

    # 11. Pi Cycle confirmed top override
    # When gap is 0 or crossed, it is a confirmed top. Make sure score is at least 85.
    pi_cross = bool(isinstance(pi_raw, dict) and pi_raw.get('cross'))
    if pi_cross and final is not None:
        final = max(final, 85)

    return {
        'onchain_avg': oc_avg,
        'tech_avg': tech_avg,
        'final_score': final,
        'phase': phase,
        'top_signal': top_signal,
        'bot_signal': bot_signal,
        'utilities': utilities,
        'normalized_scores': {k: round(v) for k, v in normalized.items()
                              if v is not None and k not in ('cipherb_weekly', 'cipherb_daily')},
        'tiz_score': tiz_score,
        'tiz_days': tiz_days,
        'tiz_maturity': tiz_maturity,
        'tiz_calibration': tiz_calibration,
        'oc_coherence': round(oc_coherence, 3),
        'coh_factor': coh_factor,
        'pi_cross': pi_cross,
        'w_top': round(w_top, 3),
        'w_bot': round(w_bot, 3),
        'w_neutral': round(w_neutral, 3),
    }
