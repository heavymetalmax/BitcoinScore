"""ML-based risk scorer (Random Forest trained on historical metric scores → 90d return).

predict_risk_score(current_scores) → int 0-100
  0  = very low risk  (model: ~90% chance of positive 90d return)
  100 = very high risk (model: ~10% chance of positive 90d return)

Formula: risk = round((1 - P(positive_90d_return)) × 100)

Model is trained by:
  python3 tools/build_ml_training_data.py
  python3 tools/train_ml_scorer.py
"""
import os, pickle
import numpy as np

_ROOT       = os.path.join(os.path.dirname(__file__), '..')
_MODEL_PATH = os.path.join(_ROOT, 'data', 'ml_scorer.pkl')

FEATURE_KEYS = [
    'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'mayer_multiple',
    'asopr', 'cipherb_weekly', 'cipherb_daily', 'fear_greed', 'm2_yoy',
]

_cache = None


def _load():
    global _cache
    if _cache is None:
        if not os.path.exists(_MODEL_PATH):
            return None
        with open(_MODEL_PATH, 'rb') as f:
            _cache = pickle.load(f)
    return _cache


def predict_risk_score(current_scores: dict):
    """
    Returns 0-100 ML risk score, or None if model file not found.

    current_scores: dict with metric score keys (0-100 each, None allowed).
    Missing features are imputed by the pipeline's median imputer.
    """
    model_data = _load()
    if model_data is None:
        return None

    pipeline = model_data['pipeline']
    keys     = model_data.get('feature_keys', FEATURE_KEYS)

    row = [current_scores.get(k) for k in keys]
    row = [v if v is not None else np.nan for v in row]

    prob_positive = pipeline.predict_proba(np.array([row], dtype=float))[0][1]
    return round((1 - prob_positive) * 100)


def model_info():
    """Return metadata dict, or None if model not loaded."""
    m = _load()
    if not m:
        return None
    return {
        'n_samples':  m.get('n_samples'),
        'cv_roc_auc': m.get('cv_roc_auc'),
        'trained_at': m.get('trained_at'),
    }
