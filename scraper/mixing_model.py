"""V5.13 mixing model — inference layer.

Feature taxonomy (58 features, matches train_mixing_model.py FEATURE_COLS):
  PRICE_PCT     (1) — pct_btc_price
  MICRO        (13) — risk-oriented causal percentile per metric
  BASKET HOT    (3) — fraction of cluster metrics in danger zone (> 0.75)
  BASKET COLD   (3) — fraction of cluster metrics in safe zone (< 0.25)
  DIVERGENCE    (3) — dy_vs_qc, mc_vs_qc, all_spread
  COHERENCE     (2) — qc_std, dy_std
  EXTREME       (3) — count_danger, count_safe, extreme_bias
  VELOCITY     (15) — multi-horizon basket deltas 7d/30d/90d/180d + accelerations
  PRICE_DIV     (3) — price_vs_qc, price_vs_dy, price_vs_all
  V3_PHASE     (11) — w_top/w_bot/v3_score/flags/velocities/peaks + ath_divergence
"""
import bisect
import datetime
import json
import math
import os
import pickle
import numpy as np
from pathlib import Path

_MODEL      = None
_FEAT_COLS  = None
_HIST       = None
_MEDIANS    = None
_MODEL_PATH = 'data/v5_mixing_model.pkl'

QC_METRICS  = ['nupl', 'mvrv', 'rhodl_ratio', 'cvdd_ratio', 'puell']
DY_METRICS  = ['cipherb_daily', 'mayer_multiple', 'fear_greed', 'funding_rate']
MC_METRICS  = ['m2_yoy', 'yield_curve', 'dxy']
ALL_METRICS = QC_METRICS + DY_METRICS + MC_METRICS + ['lth_supply_pct']

RISK_INVERTED = {'m2_yoy', 'yield_curve', 'lth_supply_pct'}

# days → tolerance in days (7d and 30d basket removed — too noisy, see V5.14)
LOOKBACK_HORIZONS = {30: 7, 90: 14, 180: 21}


# ── Model loading ─────────────────────────────────────────────────────────────

def _load():
    global _MODEL, _FEAT_COLS, _HIST, _MEDIANS
    if _MODEL is not None:
        return True
    path = Path(_MODEL_PATH)
    if not path.exists():
        return False
    try:
        with open(path, 'rb') as f:
            p = pickle.load(f)
        _MODEL     = p['model']
        _FEAT_COLS = p['feature_cols']
        _HIST      = p.get('metric_history', {})
        _MEDIANS   = p.get('col_medians')
        return True
    except Exception as e:
        print(f'Warning: V5A model load failed ({_MODEL_PATH}) — {e}')
        return False


# ── Feature helpers ───────────────────────────────────────────────────────────

def _pct_rank(metric, value):
    if _HIST is None or value is None:
        return None
    buf = _HIST.get(metric)
    if not buf:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None
    return bisect.bisect_right(buf, v) / len(buf)


def _risk_pct(metric, value):
    """Risk-oriented percentile: high = high risk."""
    raw = _pct_rank(metric, value)
    if raw is None:
        return None
    return (1.0 - raw) if metric in RISK_INVERTED else raw


def _avg(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def _std(vals):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None
    mean = sum(v) / len(v)
    return math.sqrt(sum((x - mean) ** 2 for x in v) / len(v))


def _get_lookback(target_date: datetime.date | None = None):
    """Read scores.json and return lookback data for all velocity horizons.

    Returns dict with keys:
      For each horizon N in {7, 30, 90, 180}:
        qc_{N}d, dy_{N}d, mc_{N}d  — basket averages at N days ago
        w_bot_{N}d                  — w_bot at N days ago
        w_top_{N}d                  — w_top at N days ago (30d used for delta_w_top_30d)
        v3_score_{N}d               — v3 final_score at N days ago (30d used)
      Also:
        w_top_peak_90d, v3_peak_90d — rolling max over last 90 days
    """
    scores_path = 'data/history/scores.json'
    if not os.path.exists(scores_path):
        return {}
    try:
        with open(scores_path, encoding='utf-8') as f:
            history = json.load(f)
    except Exception:
        return {}

    if target_date is None:
        target_date = datetime.date.today()

    # Index rows by date for fast window lookup
    date_rows: dict[str, dict] = {}
    for row in history:
        d_str = row.get('date', '')[:10]
        if d_str:
            date_rows[d_str] = row

    # Best match row and current min delta for each horizon
    best = {n: (None, tol + 1) for n, tol in LOOKBACK_HORIZONS.items()}
    w_top_peak_90 = None
    v3_peak_90 = None
    target_90_start = target_date - datetime.timedelta(days=90)

    for row in history:
        d_str = row.get('date', '')
        if not d_str:
            continue
        try:
            d = datetime.date.fromisoformat(d_str[:10])
        except ValueError:
            continue

        # Update best match for each lookback horizon
        for n, tol in LOOKBACK_HORIZONS.items():
            target_n = target_date - datetime.timedelta(days=n)
            delta = abs((d - target_n).days)
            if delta < best[n][1]:
                best[n] = (row, delta)

        # 90d rolling peak of w_top and v3_score
        if target_90_start <= d < target_date:
            wt = row.get('w_top')
            vs = row.get('final_score')
            if wt is not None:
                w_top_peak_90 = wt if w_top_peak_90 is None else max(w_top_peak_90, wt)
            if vs is not None:
                v3_peak_90 = vs if v3_peak_90 is None else max(v3_peak_90, vs)

    out = {'w_top_peak_90d': w_top_peak_90, 'v3_peak_90d': v3_peak_90}

    for n, tol in LOOKBACK_HORIZONS.items():
        row_n, delta_n = best[n]
        if row_n is None or delta_n > tol:
            continue
        # Smoothed basket: average over ±3 days around the lookback date.
        # Prevents single-day price spikes in the reference date from creating
        # abrupt jumps in velocity features (e.g. July 12 anomaly).
        target_n = target_date - datetime.timedelta(days=n)
        qc_vals, dy_vals, mc_vals = [], [], []
        for day_delta in range(-3, 4):
            d_w = (target_n + datetime.timedelta(days=day_delta)).isoformat()
            r_w = date_rows.get(d_w)
            if r_w:
                rp_w = {m: _risk_pct(m, r_w.get(m)) for m in ALL_METRICS}
                qc_w = _avg([rp_w.get(m) for m in QC_METRICS])
                dy_w = _avg([rp_w.get(m) for m in DY_METRICS])
                mc_w = _avg([rp_w.get(m) for m in MC_METRICS])
                if qc_w is not None: qc_vals.append(qc_w)
                if dy_w is not None: dy_vals.append(dy_w)
                if mc_w is not None: mc_vals.append(mc_w)
        out[f'qc_{n}d']       = sum(qc_vals) / len(qc_vals) if qc_vals else None
        out[f'dy_{n}d']       = sum(dy_vals) / len(dy_vals) if dy_vals else None
        out[f'mc_{n}d']       = sum(mc_vals) / len(mc_vals) if mc_vals else None
        out[f'w_bot_{n}d']    = row_n.get('w_bot')
        out[f'w_top_{n}d']    = row_n.get('w_top')
        out[f'v3_score_{n}d'] = row_n.get('final_score')

    return out


# ── SHAP via XGBoost native pred_contribs ────────────────────────────────────

def _shap_top5(feat_arr: np.ndarray, feat_cols: list) -> list | None:
    """Return top-5 SHAP contributions using XGBoost's native pred_contribs."""
    try:
        import xgboost as xgb
        dmat = xgb.DMatrix(feat_arr)
        shap_vals = _MODEL.get_booster().predict(dmat, pred_contribs=True)
        contribs = shap_vals[0][:-1]  # exclude bias term
        top5_idx = np.argsort(np.abs(contribs))[-5:][::-1]
        return [
            {'feature': feat_cols[i], 'contribution': round(float(contribs[i]), 2)}
            for i in top5_idx
        ]
    except Exception:
        return None


# ── Confidence score (post-processing, not a training feature) ────────────────

def compute_confidence(ctx: dict, v3_score: float | None, v5_score: float,
                       feat_cols: list) -> float:
    """Compute confidence in V5 prediction — 4 components, weighted average."""
    # 1. Metric coherence: how aligned are all 13 MICRO percentiles?
    micro_keys = [k for k in feat_cols if k.startswith('pct_') and k != 'pct_btc_price']
    micro_vals = [ctx[k] for k in micro_keys if ctx.get(k) is not None]
    if len(micro_vals) >= 4:
        std = _std(micro_vals)
        metric_coherence = 1.0 - (std if std is not None else 0.3)
    else:
        metric_coherence = 0.5
    metric_coherence = max(0.0, min(1.0, metric_coherence))

    # 2. Historical familiarity: how close is current vector to training median?
    if _MEDIANS is not None and feat_cols:
        curr_vec = np.array([ctx.get(f, np.nan) for f in feat_cols], dtype=np.float32)
        medians = np.array(_MEDIANS, dtype=np.float32)
        mask = ~np.isnan(curr_vec)
        if mask.sum() > 0:
            dist = float(np.mean(np.abs(curr_vec[mask] - medians[mask]))) / 0.5
            historical_familiarity = max(0.0, 1.0 - dist)
        else:
            historical_familiarity = 0.5
    else:
        historical_familiarity = 0.5

    # 3. V3/V5 agreement
    if v3_score is not None:
        v3_v5_agreement = 1.0 - abs(v3_score - v5_score) / 100.0
    else:
        v3_v5_agreement = 0.5

    # Phase stability not available at inference without scores.json scan —
    # use a fixed moderate weight so confidence is still useful
    phase_stability = 0.6

    confidence = (0.35 * metric_coherence
                + 0.25 * phase_stability
                + 0.25 * historical_familiarity
                + 0.15 * v3_v5_agreement)
    return round(max(0.0, min(1.0, confidence)), 3)


# ── Main inference ────────────────────────────────────────────────────────────

def predict(
    raw_metrics: dict,
    target_date: datetime.date | None = None,
    w_top: float | None = None,
    w_bot: float | None = None,
    v3_score: float | None = None,
) -> dict | None:
    """Predict 365-day forward risk score (0-100).

    Returns dict {'score': float, 'confidence': float, 'shap_top5': list|None}
    or None if model unavailable.

    raw_metrics keys: nupl, mvrv, rhodl_ratio, cvdd_ratio, puell,
                      cipherb_daily, mayer_multiple, fear_greed, funding_rate,
                      m2_yoy, lth_supply_pct, yield_curve, dxy, btc_price
    w_top, w_bot, v3_score: V3 phase context.
    """
    if not _load():
        return None

    # BTC price percentile: ATH = 1.0 = max uncharted-territory risk
    btc_price = raw_metrics.get('btc_price')
    pct_btc_price = _pct_rank('btc_price', btc_price)

    # Risk-oriented percentile per metric
    rp = {m: _risk_pct(m, raw_metrics.get(m)) for m in ALL_METRICS}

    # Flat risk-avg baskets
    qc = _avg([rp.get(m) for m in QC_METRICS])
    dy = _avg([rp.get(m) for m in DY_METRICS])
    mc = _avg([rp.get(m) for m in MC_METRICS])

    # Per-basket threshold counts
    def _hot_cold(metric_list, thresh_hot=0.75, thresh_cold=0.25):
        vals = [rp.get(m) for m in metric_list if rp.get(m) is not None]
        if not vals:
            return None, None
        return (sum(1 for v in vals if v > thresh_hot) / len(vals),
                sum(1 for v in vals if v < thresh_cold) / len(vals))

    qc_hot, qc_cold = _hot_cold(QC_METRICS)
    dy_hot, dy_cold = _hot_cold(DY_METRICS)
    mc_hot, mc_cold = _hot_cold(MC_METRICS)

    # Cross-cluster divergences
    dy_vs_qc  = (dy - qc)  if dy  is not None and qc is not None else None
    mc_vs_qc  = (mc - qc)  if mc  is not None and qc is not None else None
    all_vals  = [v for v in [qc, dy, mc] if v is not None]
    all_spread = (max(all_vals) - min(all_vals)) if len(all_vals) >= 2 else None

    # Coherence
    qc_std = _std([rp.get(m) for m in QC_METRICS])
    dy_std = _std([rp.get(m) for m in DY_METRICS])

    # Extreme concentration
    all_rp = [rp[m] for m in ALL_METRICS if rp.get(m) is not None]
    if all_rp:
        count_danger = sum(1 for x in all_rp if x > 0.80) / len(all_rp)
        count_safe   = sum(1 for x in all_rp if x < 0.20) / len(all_rp)
        extreme_bias = count_danger - count_safe
    else:
        count_danger = count_safe = extreme_bias = None

    # Multi-horizon lookback
    lb = _get_lookback(target_date)

    def _d(curr, key):
        prev = lb.get(key)
        return (curr - prev) if curr is not None and prev is not None else None

    # Basket velocity: 90d / 180d only (7d/30d removed in V5.14 — noisy, override fundamentals)
    delta_qc_90d  = _d(qc, 'qc_90d')
    delta_dy_90d  = _d(dy, 'dy_90d')
    delta_mc_90d  = _d(mc, 'mc_90d')
    delta_qc_180d = _d(qc, 'qc_180d')
    delta_dy_180d = _d(dy, 'dy_180d')
    delta_mc_180d = _d(mc, 'mc_180d')

    # W_bot velocity — 90d only (30d removed: rapid phase transitions create V5 spikes)
    delta_w_bot_90d = _d(w_bot, 'w_bot_90d')

    # V3 30d velocity and peak features
    w_top_30d    = lb.get('w_top_30d')
    v3_score_30d = lb.get('v3_score_30d')
    w_top_peak   = lb.get('w_top_peak_90d')
    v3_peak      = lb.get('v3_peak_90d')

    delta_w_top_30d = _d(w_top, 'w_top_30d')
    delta_v3_30d    = _d(v3_score, 'v3_score_30d')
    w_top_vs_peak   = (w_top - w_top_peak) if w_top is not None and w_top_peak is not None else None
    v3_vs_peak      = (v3_score - v3_peak) if v3_score is not None and v3_peak is not None else None

    # Price divergence from metric baskets
    all_rp_avg = _avg([rp[m] for m in ALL_METRICS if rp.get(m) is not None])
    price_vs_qc  = (pct_btc_price - qc)         if pct_btc_price is not None and qc         is not None else None
    price_vs_dy  = (pct_btc_price - dy)         if pct_btc_price is not None and dy         is not None else None
    price_vs_all = (pct_btc_price - all_rp_avg) if pct_btc_price is not None and all_rp_avg is not None else None

    # V3 phase context
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
        # Velocity long-horizon (90d/180d)
        'delta_qc_90d':  delta_qc_90d,  'delta_dy_90d':  delta_dy_90d,  'delta_mc_90d':  delta_mc_90d,
        'delta_qc_180d': delta_qc_180d, 'delta_dy_180d': delta_dy_180d, 'delta_mc_180d': delta_mc_180d,
        'delta_w_bot_90d': delta_w_bot_90d,
        # Price divergence
        'price_vs_qc': price_vs_qc, 'price_vs_dy': price_vs_dy, 'price_vs_all': price_vs_all,
        # V3 phase context
        'w_top': w_top, 'w_bot': w_bot, 'v3_score': v3_score,
        'phase_is_top': _phase_is_top, 'phase_is_bot': _phase_is_bot,
        'delta_w_top_30d': delta_w_top_30d, 'delta_v3_30d': delta_v3_30d,
        'w_top_vs_peak': w_top_vs_peak, 'v3_vs_peak': v3_vs_peak,
        'ath_divergence':    (pct_btc_price * max(0.0, -w_top_vs_peak))
                             if pct_btc_price is not None and w_top_vs_peak is not None else None,
        'ath_v3_divergence': (pct_btc_price * max(0.0, -v3_vs_peak / 30.0))
                             if pct_btc_price is not None and v3_vs_peak is not None else None,
    }

    feat_cols = _FEAT_COLS
    raw_vals = [
        float(ctx[c]) if ctx.get(c) is not None else float('nan')
        for c in feat_cols
    ]
    if _MEDIANS:
        raw_vals = [v if not math.isnan(v) else _MEDIANS[i] for i, v in enumerate(raw_vals)]

    feat = np.array(raw_vals, dtype=np.float32).reshape(1, -1)
    try:
        score = float(_MODEL.predict(feat)[0])
        score = max(1.0, min(99.0, score))
    except Exception:
        return None

    confidence = compute_confidence(ctx, v3_score, score, feat_cols)
    shap_top5  = _shap_top5(feat, feat_cols)

    return {'score': score, 'confidence': confidence, 'shap_top5': shap_top5}


def is_available() -> bool:
    return _load()
