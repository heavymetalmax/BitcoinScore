"""Utility Evaluator for BitcoinScore metrics (Scoring V3.1).

Computes dynamic utility coefficients U_i in [0.1, 1.0] for each metric using
a continuous mixture of three state-space weights (w_top, w_bottom, w_neutral)
derived from cycle proximity metrics. Supports dynamic JSON weight loading.
"""

import os
import json
import math

# Default baseline relevance profiles
RELEVANCE_PROFILES = {
    # On-Chain Bottom-focused
    'cvdd_ratio':          {'BOTTOM': 1.0, 'NEUTRAL': 0.4, 'TOP': 0.1},
    'puell':               {'BOTTOM': 1.0, 'NEUTRAL': 0.5, 'TOP': 0.2},
    # aSOPR: backtest showed ~50-78 scores at genuine bottoms AND tops (range-bound oscillator,
    # not regime-discriminating) — downgraded from BOTTOM=0.9 to symmetric low weight.
    'asopr':               {'BOTTOM': 0.4, 'NEUTRAL': 0.5, 'TOP': 0.5},
    # General On-Chain (high relevance at both extremes)
    'nupl':                {'BOTTOM': 1.0, 'NEUTRAL': 0.7, 'TOP': 1.0},
    'mvrv_z_score':        {'BOTTOM': 1.0, 'NEUTRAL': 0.7, 'TOP': 1.0},
    # rhodl: missed 2019 peak (scored 38 when expected ~80+); raised TOP weight.
    'rhodl_ratio':         {'BOTTOM': 0.6, 'NEUTRAL': 0.6, 'TOP': 0.7},
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
    'pi_gap':              {'BOTTOM': 0.1, 'NEUTRAL': 0.5, 'TOP': 1.0}
}

_WEIGHTS_PATH = 'data/v3_relevance_weights.json'


def load_relevance_weights():
    """Load optimized relevance weights from JSON if available, overriding defaults."""
    global RELEVANCE_PROFILES
    if os.path.exists(_WEIGHTS_PATH):
        try:
            with open(_WEIGHTS_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if k in RELEVANCE_PROFILES and isinstance(v, dict):
                            RELEVANCE_PROFILES[k].update(v)
            # We don't print on every import to avoid polluting output, but weights are updated.
        except Exception:
            pass


# Load weights on module initialization
load_relevance_weights()


def compute_std(values):
    """Compute standard deviation of a list of numeric values."""
    if not values or len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def evaluate_utility(metric, normalized_score, w_top, w_bot, w_neutral, tiz_maturity=None, recent_scores=None):
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
    
    # 3. Time-in-Zone Temporal Scaling
    # For bottom-focused indicators, suppress utility if we are in bottom territory and early (tiz_maturity < 0.25)
    if w_bot > 0.5 and tiz_maturity is not None and tiz_maturity < 0.25:
        bottom_metrics = {'cvdd_ratio', 'puell', 'asopr', 'nupl', 'mvrv_z_score'}
        if canonical_key in bottom_metrics:
            utility *= (0.40 + 0.60 * (tiz_maturity / 0.25))
            
    # Clamp utility to [0.1, 1.0] range
    return max(0.1, min(1.0, utility))


def evaluate_all_utilities_continuous(normalized_scores, w_top, w_bot, w_neutral, tiz_maturity=None, metrics_history=None):
    """Evaluate utility coefficients for all metrics using continuous weights."""
    utilities = {}
    history = metrics_history or {}
    for k, v in normalized_scores.items():
        recent = history.get(k)
        utilities[k] = evaluate_utility(k, v, w_top, w_bot, w_neutral, tiz_maturity, recent)
    return utilities
