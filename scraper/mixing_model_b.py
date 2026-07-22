"""V5B Forward Risk Model — inference layer.

Answers: "What is the expected maximum drawdown over the next 365 days?"

Output: expected max drawdown % (0-80+), where:
  0–5   = buy zone  (historically price only goes up from here)
  15–35 = neutral / moderate risk
  60+   = danger zone (major bear market followed in similar conditions)

Uses the same 49-feature vector and _get_lookback() infrastructure as mixing_model.py.
The model (v5b_model.pkl) was trained on max_drawdown_365d labels — fully independent
of V3 and V5A cycle-position scores.
"""
import datetime
import math
import pickle
from pathlib import Path

import numpy as np

# Reuse constants and infrastructure from V5A — no duplication
from scraper.mixing_model import (
    QC_METRICS, DY_METRICS, MC_METRICS, ALL_METRICS, RISK_INVERTED,
    LOOKBACK_HORIZONS,
    _load,          # ensures V5A model (and _HIST) is loaded
    _pct_rank, _risk_pct, _avg, _std,
    _get_lookback,
)

_MODEL_B_PATH = 'data/v5b_model.pkl'
_MODEL_B:    object | None = None
_FEAT_COLS_B: list | None = None
_MEDIANS_B:  np.ndarray | None = None


def _load_b() -> bool:
    global _MODEL_B, _FEAT_COLS_B, _MEDIANS_B
    if _MODEL_B is not None:
        return True
    path = Path(_MODEL_B_PATH)
    if not path.exists():
        return False
    try:
        with open(path, 'rb') as f:
            p = pickle.load(f)
        _MODEL_B     = p['model']
        _FEAT_COLS_B = p['feature_cols']
        _MEDIANS_B   = p.get('col_medians')
        # V5A must be loaded so _HIST (metric history) is available for percentile ranks
        _load()
        return True
    except Exception as e:
        print(f'Warning: V5B model load failed ({_MODEL_B_PATH}) — {e}')
        return False


def predict_b(
    raw_metrics: dict,
    target_date: datetime.date | None = None,
    w_top: float | None = None,
    w_bot: float | None = None,
    v3_score: float | None = None,
) -> dict | None:
    """Predict expected max drawdown (%) over the next 365 days.

    raw_metrics keys: nupl, mvrv, rhodl_ratio, cvdd_ratio, puell,
                      cipherb_daily, mayer_multiple, fear_greed, funding_rate,
                      m2_yoy, lth_supply_pct, yield_curve, dxy, btc_price
    w_top, w_bot, v3_score — V3 phase context (accepted separately OR via
                              raw_metrics keys 'v3_w_top', 'v3_w_bot', 'v3_score').
    Returns {'score': float, 'label': 'max_drawdown_365d'} or None.
    """
    if not _load_b():
        return None

    if target_date is None:
        target_date = datetime.date.today()

    # Accept V3 context from raw_metrics if not passed explicitly
    if w_top is None:
        w_top = raw_metrics.get('v3_w_top') or raw_metrics.get('w_top')
    if w_bot is None:
        w_bot = raw_metrics.get('v3_w_bot') or raw_metrics.get('w_bot')
    if v3_score is None:
        v3_score = raw_metrics.get('v3_score') or raw_metrics.get('final_score')

    # ── Feature building — mirrors mixing_model.predict() exactly ─────────────

    btc_price = raw_metrics.get('btc_price')
    pct_btc_price = _pct_rank('btc_price', btc_price)

    rp = {m: _risk_pct(m, raw_metrics.get(m)) for m in ALL_METRICS}

    qc = _avg([rp.get(m) for m in QC_METRICS])
    dy = _avg([rp.get(m) for m in DY_METRICS])
    mc = _avg([rp.get(m) for m in MC_METRICS])

    def _hot_cold(metric_list, thresh_hot=0.75, thresh_cold=0.25):
        vals = [rp.get(m) for m in metric_list if rp.get(m) is not None]
        if not vals:
            return None, None
        return (sum(1 for v in vals if v > thresh_hot) / len(vals),
                sum(1 for v in vals if v < thresh_cold) / len(vals))

    qc_hot, qc_cold = _hot_cold(QC_METRICS)
    dy_hot, dy_cold = _hot_cold(DY_METRICS)
    mc_hot, mc_cold = _hot_cold(MC_METRICS)

    dy_vs_qc   = (dy - qc) if dy  is not None and qc is not None else None
    mc_vs_qc   = (mc - qc) if mc  is not None and qc is not None else None
    all_vals   = [v for v in [qc, dy, mc] if v is not None]
    all_spread = (max(all_vals) - min(all_vals)) if len(all_vals) >= 2 else None

    qc_std = _std([rp.get(m) for m in QC_METRICS])
    dy_std = _std([rp.get(m) for m in DY_METRICS])

    all_rp = [rp[m] for m in ALL_METRICS if rp.get(m) is not None]
    if all_rp:
        count_danger = sum(1 for x in all_rp if x > 0.80) / len(all_rp)
        count_safe   = sum(1 for x in all_rp if x < 0.20) / len(all_rp)
        extreme_bias = count_danger - count_safe
    else:
        count_danger = count_safe = extreme_bias = None

    lb = _get_lookback(target_date)

    def _d(curr, key):
        prev = lb.get(key)
        return (curr - prev) if curr is not None and prev is not None else None

    delta_qc_90d  = _d(qc, 'qc_90d')
    delta_dy_90d  = _d(dy, 'dy_90d')
    delta_mc_90d  = _d(mc, 'mc_90d')
    delta_qc_180d = _d(qc, 'qc_180d')
    delta_dy_180d = _d(dy, 'dy_180d')
    delta_mc_180d = _d(mc, 'mc_180d')

    delta_w_bot_90d = _d(w_bot, 'w_bot_90d')

    w_top_peak   = lb.get('w_top_peak_90d')
    v3_peak      = lb.get('v3_peak_90d')
    delta_w_top_30d = _d(w_top, 'w_top_30d')
    delta_v3_30d    = _d(v3_score, 'v3_score_30d')
    w_top_vs_peak   = (w_top    - w_top_peak) if w_top    is not None and w_top_peak is not None else None
    v3_vs_peak      = (v3_score - v3_peak)    if v3_score is not None and v3_peak    is not None else None

    all_rp_avg = _avg([rp[m] for m in ALL_METRICS if rp.get(m) is not None])
    price_vs_qc  = (pct_btc_price - qc)         if pct_btc_price is not None and qc         is not None else None
    price_vs_dy  = (pct_btc_price - dy)         if pct_btc_price is not None and dy         is not None else None
    price_vs_all = (pct_btc_price - all_rp_avg) if pct_btc_price is not None and all_rp_avg is not None else None

    _phase_is_top = 1.0 if (w_top is not None and w_top > 0.4) else 0.0
    _phase_is_bot = 1.0 if (w_bot is not None and w_bot > 0.4) else 0.0

    ctx = {
        'pct_btc_price': pct_btc_price,
        **{f'pct_{m}': rp[m] for m in ALL_METRICS},
        'qc_hot': qc_hot, 'dy_hot': dy_hot, 'mc_hot': mc_hot,
        'qc_cold': qc_cold, 'dy_cold': dy_cold, 'mc_cold': mc_cold,
        'dy_vs_qc': dy_vs_qc, 'mc_vs_qc': mc_vs_qc, 'all_spread': all_spread,
        'qc_std': qc_std, 'dy_std': dy_std,
        'count_danger': count_danger, 'count_safe': count_safe, 'extreme_bias': extreme_bias,
        'delta_qc_90d':  delta_qc_90d,  'delta_dy_90d':  delta_dy_90d,  'delta_mc_90d':  delta_mc_90d,
        'delta_qc_180d': delta_qc_180d, 'delta_dy_180d': delta_dy_180d, 'delta_mc_180d': delta_mc_180d,
        'delta_w_bot_90d': delta_w_bot_90d,
        'price_vs_qc': price_vs_qc, 'price_vs_dy': price_vs_dy, 'price_vs_all': price_vs_all,
        'w_top': w_top, 'w_bot': w_bot, 'v3_score': v3_score,
        'phase_is_top': _phase_is_top, 'phase_is_bot': _phase_is_bot,
        'delta_w_top_30d': delta_w_top_30d, 'delta_v3_30d': delta_v3_30d,
        'w_top_vs_peak': w_top_vs_peak, 'v3_vs_peak': v3_vs_peak,
        'ath_divergence':    (pct_btc_price * max(0.0, -w_top_vs_peak))
                             if pct_btc_price is not None and w_top_vs_peak is not None else None,
        'ath_v3_divergence': (pct_btc_price * max(0.0, -v3_vs_peak / 30.0))
                             if pct_btc_price is not None and v3_vs_peak is not None else None,
    }

    raw_vals = [
        float(ctx[c]) if ctx.get(c) is not None else float('nan')
        for c in _FEAT_COLS_B
    ]
    if _MEDIANS_B is not None:
        raw_vals = [v if not math.isnan(v) else float(_MEDIANS_B[i])
                    for i, v in enumerate(raw_vals)]

    feat = np.array(raw_vals, dtype=np.float32).reshape(1, -1)
    try:
        score = float(_MODEL_B.predict(feat)[0])
        score = round(max(0.0, min(100.0, score)), 1)
    except Exception:
        return None

    return {'score': score, 'label': 'max_drawdown_365d'}
