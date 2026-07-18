"""Utility Evaluator for BitcoinScore metrics (Scoring V3.1).

Computes dynamic utility coefficients U_i in [0.1, 1.0] for each metric using
a continuous mixture of three state-space weights (w_top, w_bottom, w_neutral)
derived from cycle proximity metrics. Relevance weights are loaded from the
model pickle (single source of truth); Python defaults are the cold-start fallback.
"""

import os
import math

# Metrics whose utility should rise near cycle top (top-focused indicators)
TOP_METRICS = {'cipherb', 'mayer_multiple', 'pi_gap', 'funding_rate', 'lth_supply'}
# Metrics whose utility should rise in post-peak/accumulation (bottom-focused indicators)
BOT_METRICS = {'cvdd_ratio', 'puell', 'nupl', 'mvrv_z_score', 'rhodl_ratio'}


def _cycle_top_bias(cycle_day):
    """Asymmetric Gaussian proxy for cycle-top proximity [0.05, 1.0].

    Peaks at ATH (day ~528), decays naturally on both sides:
      sigma_left  = 230  — wide left tail: slow accumulation from halving to ATH
      sigma_right = 120  — tight right tail: faster post-ATH bear collapse

    At the bear market bottom (~day 920) bias ≈ 0.05 (floor),
    giving TOP-metrics their minimum utility and BOT-metrics their maximum.
    """
    if cycle_day is None:
        return 0.5
    d = float(cycle_day)
    peak = 528.0
    # sigma_left=250: bias≈0.10 at halving day (slow bull build-up)
    # sigma_right=160: bias reaches floor ~0.05 at day 920 (bear market bottom)
    sigma = 250.0 if d <= peak else 160.0
    return max(0.05, math.exp(-0.5 * ((d - peak) / sigma) ** 2))


def halving_cycle_multiplier(canonical_key, cycle_day):
    """Utility multiplier [0.85, 1.15] based on cycle position.

    TOP_METRICS boosted as we near the peak; BOT_METRICS boosted post-peak.
    Macro/tactical metrics (yield_curve, m2, etf_flows, etc.) are unaffected.
    """
    bias = _cycle_top_bias(cycle_day)
    if canonical_key in TOP_METRICS:
        return 0.85 + bias * 0.30
    elif canonical_key in BOT_METRICS:
        return 1.15 - bias * 0.30
    return 1.0


# Default baseline relevance profiles — cold-start fallback only.
# The authoritative values come from the trained model pickle (metric_relevance key).
RELEVANCE_PROFILES = {
    # On-Chain Bottom-focused
    # cvdd_ratio: disc=+41.1 normalized — good discriminator at both extremes.
    'cvdd_ratio':          {'BOTTOM': 1.0, 'NEUTRAL': 0.4, 'TOP': 0.1},
    'puell':               {'BOTTOM': 1.0, 'NEUTRAL': 0.5, 'TOP': 0.2},
    # aSOPR: Fisher=0.46, IC(365d)=-0.059. Daily oscillator around 1.0 — near-zero
    # cycle discrimination (bottom_avg=1.009 vs top_avg=1.014, Δ=0.005 raw units).
    # Excluded from bottom_confluence.py for the same reason. Weight=0 removes it
    # from final_score; raw value still stored in scores.json for capitulation alerts
    # (asopr < 1.0 = selling at a loss = short-term buy confirmation signal).
    'asopr':               {'BOTTOM': 0.0, 'NEUTRAL': 0.0, 'TOP': 0.0},
    # General On-Chain (high relevance at both extremes)
    'nupl':                {'BOTTOM': 1.0, 'NEUTRAL': 0.7, 'TOP': 1.0},
    'mvrv_z_score':        {'BOTTOM': 1.0, 'NEUTRAL': 0.7, 'TOP': 1.0},
    # rhodl: disc=+16.2 on confirmed extremes — weakest top discriminator.
    # Avg_top=45, avg_bot=29: reads flat 35-60 at ALL confirmed tops (macro and sub).
    # Only works for Dec-2017 macro top; reduces score at all other tops.
    # Reducing TOP weight is clearly justified; BOTTOM weight kept (reads low at bottoms).
    'rhodl_ratio':         {'BOTTOM': 0.6, 'NEUTRAL': 0.5, 'TOP': 0.20},
    # Tech/Macro Top-focused
    'cipherb':             {'BOTTOM': 0.4, 'NEUTRAL': 0.7, 'TOP': 1.0},
    'mayer_multiple':      {'BOTTOM': 0.4, 'NEUTRAL': 0.6, 'TOP': 1.0},
    'fear_greed':          {'BOTTOM': 0.9, 'NEUTRAL': 0.5, 'TOP': 0.9},
    # Tactical / Flows
    'etf_flows':           {'BOTTOM': 0.3, 'NEUTRAL': 0.8, 'TOP': 0.4},
    # Macro (contrarian, intentionally noisy) — downweighted at cycle extremes.
    # Both metrics behave counterintuitively at tops/bottoms (inverted logic + lag).
    'yield_curve_spread':  {'BOTTOM': 0.3, 'NEUTRAL': 0.6, 'TOP': 0.2},
    'm2_yoy':              {'BOTTOM': 0.3, 'NEUTRAL': 0.6, 'TOP': 0.2},
    # Pi Cycle Gap
    'pi_gap':              {'BOTTOM': 0.1, 'NEUTRAL': 0.5, 'TOP': 1.0},
    # Funding Rate: longs overheated = high risk; more relevant at TOP than BOTTOM
    'funding_rate':        {'BOTTOM': 0.3, 'NEUTRAL': 0.5, 'TOP': 0.8},
    # LTH Supply: distribution is an early TOP warning; accumulation validates BOTTOM
    'lth_supply':          {'BOTTOM': 0.5, 'NEUTRAL': 0.6, 'TOP': 0.9},
}

_MODEL_PATH = 'data/v3_phase_model.pkl'


def load_relevance_weights():
    """No-op: relevance weights are domain-specified in RELEVANCE_PROFILES above.

    The HMM model pickle is used only for phase detection (w_top/w_bot) in score.py.
    Learned metric_relevance values from the pickle are intentionally ignored because
    the HMM optimizer does not have domain constraints and produces weights that
    contradict known metric behavior (e.g. cvdd_ratio TOP=0.92 instead of 0.1).
    """
    pass


def compute_std(values):
    """Compute standard deviation of a list of numeric values."""
    if not values or len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def evaluate_utility(metric, normalized_score, w_top, w_bot, w_neutral, tiz_maturity=None, recent_scores=None, cycle_day=None):
    """Compute the utility coefficient U_i in [0.1, 1.0] for a given metric.

    Uses continuous state-space mixture weights.
    """
    if normalized_score is None:
        return 0.1  # Minimum baseline for absent/failed metrics
        
    # Translate metric names to canonical keys in RELEVANCE_PROFILES
    canonical_key = metric
    if metric == 'mvrv':
        canonical_key = 'mvrv_z_score'
    elif metric == 'puell_multiple':
        canonical_key = 'puell'
    elif metric == 'pi_cycle':
        canonical_key = 'pi_gap'
        
    # 1. Continuous state mixture base utility
    profile = RELEVANCE_PROFILES.get(canonical_key)
    if profile:
        base_utility = (
            w_top * profile.get('TOP', 0.5) +
            w_bot * profile.get('BOTTOM', 0.5) +
            w_neutral * profile.get('NEUTRAL', 0.5)
        )
    else:
        base_utility = 0.5
        
    # 2. Noise Dampening
    noise_dampening = 1.0
    if recent_scores and len(recent_scores) >= 3:
        std_val = compute_std(recent_scores)
        # Scale noise: std of 50 score points maps to 30% reduction in utility
        noise_factor = min(0.30, std_val / 166.7)
        noise_dampening = 1.0 - noise_factor
        
    utility = base_utility * noise_dampening

    # 3. Halving-cycle position modifier (±15%)
    utility *= halving_cycle_multiplier(canonical_key, cycle_day)

    # 4. Time-in-Zone Temporal Scaling
    # For bottom-focused indicators, suppress utility if we are in bottom territory and early (tiz_maturity < 0.25)
    if w_bot > 0.5 and tiz_maturity is not None and tiz_maturity < 0.25:
        bottom_metrics = {'cvdd_ratio', 'puell', 'asopr', 'nupl', 'mvrv_z_score'}
        if canonical_key in bottom_metrics:
            utility *= (0.40 + 0.60 * (tiz_maturity / 0.25))
            
    # Clamp utility to [0.1, 1.0] range
    return max(0.1, min(1.0, utility))


def evaluate_all_utilities_continuous(normalized_scores, w_top, w_bot, w_neutral, tiz_maturity=None, metrics_history=None, cycle_day=None):
    """Evaluate utility coefficients for all metrics using continuous weights."""
    utilities = {}
    history = metrics_history or {}
    for k, v in normalized_scores.items():
        recent = history.get(k)
        utilities[k] = evaluate_utility(k, v, w_top, w_bot, w_neutral, tiz_maturity, recent, cycle_day=cycle_day)
    return utilities
