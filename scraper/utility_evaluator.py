"""Utility Evaluator for BitcoinScore metrics (Scoring V3.1).

Computes dynamic utility coefficients U_i in [0.1, 1.0] for each metric using
a continuous mixture of three state-space weights (w_top, w_bottom, w_neutral)
derived from cycle proximity metrics. Relevance weights are loaded from the
model pickle (single source of truth); Python defaults are the cold-start fallback.
"""

import os
import math

# Default baseline relevance profiles — cold-start fallback only.
# The authoritative values come from the trained model pickle (metric_relevance key).
RELEVANCE_PROFILES = {
    # On-Chain Bottom-focused
    'cvdd_ratio':          {'BOTTOM': 1.0, 'NEUTRAL': 0.4, 'TOP': 0.1},
    'puell':               {'BOTTOM': 1.0, 'NEUTRAL': 0.5, 'TOP': 0.2},
    # aSOPR: Fisher separation=0.143 (bottom_mean=58.67 vs top_mean=57.81, Δ=0.86).
    # Near-zero discrimination — keep in model for data completeness but minimal weight.
    'asopr':               {'BOTTOM': 0.15, 'NEUTRAL': 0.15, 'TOP': 0.15},
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
    'pi_gap':              {'BOTTOM': 0.1, 'NEUTRAL': 0.5, 'TOP': 1.0},
    # Funding Rate: longs overheated = high risk; more relevant at TOP than BOTTOM
    'funding_rate':        {'BOTTOM': 0.3, 'NEUTRAL': 0.5, 'TOP': 0.8},
    # Dollar Index: macro headwind/tailwind; slow-moving, moderate weight at extremes
    'dxy':                 {'BOTTOM': 0.2, 'NEUTRAL': 0.5, 'TOP': 0.2},
    # LTH Supply: distribution is an early TOP warning; accumulation validates BOTTOM
    'lth_supply':          {'BOTTOM': 0.5, 'NEUTRAL': 0.6, 'TOP': 0.9},
}

_MODEL_PATH = 'data/v3_phase_model.pkl'


def load_relevance_weights():
    """Load optimized relevance weights from the model pickle (single source of truth).

    The pickle stores metric_relevance as a plain dict — same shape as RELEVANCE_PROFILES.
    Falls back to Python defaults silently if the pickle is absent or unreadable.
    Lazy import of HMMPhaseClassifier avoids circular imports when called from
    train_v3_hmm_model.py.
    """
    global RELEVANCE_PROFILES
    if not os.path.exists(_MODEL_PATH):
        return
    try:
        import pickle
        try:
            from tools.train_v3_hmm_model import HMMPhaseClassifier  # noqa: F401
        except Exception:
            pass
        with open(_MODEL_PATH, 'rb') as f:
            model_data = pickle.load(f)
        learned = model_data.get('metric_relevance')
        if isinstance(learned, dict):
            for k, v in learned.items():
                if k in RELEVANCE_PROFILES and isinstance(v, dict):
                    RELEVANCE_PROFILES[k].update(v)
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
