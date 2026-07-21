#!/usr/bin/env python3
"""Train V5.5 mixing model.

Input:   data/training_features.json  (built by build_training_features.py)
Output:  data/v5_mixing_model.pkl

V5.5 changes vs V5.4:
  - Removed BASKET_PEAK (perm imp -0.42 in V5.4 — redundant with MICRO)
  - Result: 24 features, cleaner

V5.x lesson: any basket aggregation (flat avg, weighted, peak) as separate
features is redundant when all 13 MICRO pct_* are already present.
Risk inversions correct (m2_yoy, yield_curve, lth_supply_pct) for semantic
clarity and for divergence/velocity features.

Feature categories (24 total):
  MICRO       (13) — risk-oriented causal percentile per metric
  DIVERGENCE   (3) — signed basket diffs: dy_vs_qc, mc_vs_qc, all_spread
  COHERENCE    (2) — qc_std, dy_std
  EXTREME      (3) — count_danger, count_safe, extreme_bias
  VELOCITY     (3) — 30d delta of flat risk-avg basket positions
"""
import json
import math
import pickle
import numpy as np
from pathlib import Path

FEATURE_COLS = [
    # PRICE PERCENTILE: ATH awareness — uncharted price territory = danger signal
    'pct_btc_price',
    # MICRO: risk-oriented causal percentile (m2_yoy/yield_curve/lth_supply inverted)
    'pct_nupl', 'pct_mvrv', 'pct_rhodl_ratio', 'pct_cvdd_ratio', 'pct_puell',
    'pct_cipherb_daily', 'pct_mayer_multiple', 'pct_fear_greed', 'pct_funding_rate',
    'pct_m2_yoy', 'pct_lth_supply_pct', 'pct_yield_curve', 'pct_dxy',
    # BASKET HOT: fraction of cluster metrics in danger zone (> 0.75)
    'qc_hot', 'dy_hot', 'mc_hot',
    # BASKET COLD: fraction of cluster metrics in safe zone (< 0.25)
    'qc_cold', 'dy_cold', 'mc_cold',
    # DIVERGENCE: cross-cluster relationship signals (from flat risk-avg)
    'dy_vs_qc', 'mc_vs_qc', 'all_spread',
    # COHERENCE: internal cluster spread
    'qc_std', 'dy_std',
    # EXTREME CONCENTRATION: global consensus across all metrics
    'count_danger', 'count_safe', 'extreme_bias',
    # VELOCITY LONG-HORIZON: 90d/180d only — captures cycle trend without daily noise
    # 7d/30d basket removed: too noisy, override fundamental signals, create price-risk inversions
    # delta_w_bot_30d removed: rapid phase transitions create counterintuitive V5 spikes
    'delta_qc_90d',  'delta_dy_90d',  'delta_mc_90d',
    'delta_qc_180d', 'delta_dy_180d', 'delta_mc_180d',
    'delta_w_bot_90d',
    # PRICE DIVERGENCE: price running ahead of on-chain metrics = bearish divergence / cycle exhaustion
    # Trader signal: BTC ATH but indicators NOT confirming → distribution at top
    'price_vs_qc', 'price_vs_dy', 'price_vs_all',
    # V3 PHASE CONTEXT: stacked ensemble — V5 sees everything including V3 phase
    'w_top', 'w_bot', 'v3_score', 'phase_is_top', 'phase_is_bot',
    # V3 VELOCITY / DIVERGENCE: is V3 signal strengthening or fading?
    # Critical: w_top_vs_peak = -0.532 at 2025 ATH (TOP fading while price at new high)
    'delta_w_top_30d', 'delta_v3_30d', 'w_top_vs_peak', 'v3_vs_peak',
    # COMPOUND: explicit interaction — ATH price × phase decline magnitude
    # 2025 ATH: 1.0 × 0.532 = 0.532 | 2021 ATH: 0.99 × 0 = 0  (model can now split on this directly)
    'ath_divergence', 'ath_v3_divergence',
]

ALL_METRICS = [
    'nupl', 'mvrv', 'rhodl_ratio', 'cvdd_ratio', 'puell',
    'cipherb_daily', 'mayer_multiple', 'fear_greed', 'funding_rate',
    'm2_yoy', 'lth_supply_pct', 'yield_curve', 'dxy',
]


def load_data():
    with open('data/training_features.json') as f:
        rows = json.load(f)
    X, y, dates = [], [], []
    for row in rows:
        if row.get('label') is None:
            continue
        feat = [
            float(row[col]) if row.get(col) is not None else float('nan')
            for col in FEATURE_COLS
        ]
        X.append(feat)
        y.append(float(row['label']))
        dates.append(row['date'])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32), dates


def build_metric_history(rows):
    history = {m: [] for m in ALL_METRICS + ['btc_price']}
    for row in rows:
        for m in ALL_METRICS:
            v = row.get(f'raw_{m}')
            if v is not None:
                try:
                    history[m].append(float(v))
                except (TypeError, ValueError):
                    pass
        bp = row.get('raw_btc_price')
        if bp is not None:
            try:
                history['btc_price'].append(float(bp))
            except (TypeError, ValueError):
                pass
    for m in history:
        history[m].sort()
    return history


def main():
    print('Loading features...')
    X, y, dates = load_data()
    print(f'  {len(X)} examples  ×  {X.shape[1]} features  (V5.17)')
    print(f'  Label range: {y.min():.1f} – {y.max():.1f}  mean={y.mean():.1f}')

    split = int(len(X) * 0.80)
    X_tr, X_te = X[:split], X[split:]
    y_tr, y_te = y[:split], y[split:]

    col_medians = np.nanmedian(X_tr, axis=0)
    col_medians = np.where(np.isnan(col_medians), 0.5, col_medians)

    def impute(arr):
        out = arr.copy()
        for j in range(out.shape[1]):
            mask = np.isnan(out[:, j])
            out[mask, j] = col_medians[j]
        return out

    X_tr = impute(X_tr)
    X_te = impute(X_te)

    try:
        import xgboost as xgb
        model = xgb.XGBRegressor(
            n_estimators=400, max_depth=5, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.75, min_child_weight=8,
            reg_lambda=2.0, random_state=42, tree_method='hist', verbosity=0,
        )
        model_type = 'XGBoost'
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingRegressor
        model = HistGradientBoostingRegressor(
            max_iter=400, max_depth=5, learning_rate=0.04,
            min_samples_leaf=12, random_state=42,
        )
        model_type = 'HistGradientBoosting (sklearn)'

    print(f'\nTraining {model_type} on {len(X_tr)} examples...')
    model.fit(X_tr, y_tr)

    y_pred = model.predict(X_te)
    rmse = math.sqrt(float(np.mean((y_pred - y_te) ** 2)))
    mae  = float(np.mean(np.abs(y_pred - y_te)))
    print(f'Test (last 20% chronological):  RMSE={rmse:.2f}  MAE={mae:.2f}')

    # ── Cycle extremes validation ─────────────────────────────────────────────
    with open('data/training_features.json') as f:
        all_rows = json.load(f)
    feat_by_date = {r['date']: r for r in all_rows}

    with open('data/cycle_extremes.json') as f:
        extremes = json.load(f).get('extremes', [])

    print('\nCycle extremes validation:')
    print(f'  {"Date":<12} {"Type":<7} {"Price":>10}  {"Label":>6}  {"V5":>6}  {"Err":>5}')
    errors = []
    for ex in sorted(extremes, key=lambda e: e['date']):
        d = ex['date']
        if d not in feat_by_date:
            continue
        row = feat_by_date[d]
        label = row.get('label')
        if label is None:
            continue
        feat = [float(row[c]) if row.get(c) is not None else float('nan') for c in FEATURE_COLS]
        feat = [v if not (isinstance(v, float) and math.isnan(v)) else col_medians[i]
                for i, v in enumerate(feat)]
        pred = float(model.predict(np.array([feat], dtype=np.float32))[0])
        err  = abs(pred - label)
        errors.append(err)
        flag = '⚠' if err > 20 else ''
        print(f'  {d:<12} {ex["type"]:<7} ${ex["price"]:>9,.0f}  '
              f'{label:>6.1f}  {pred:>6.1f}  {err:>5.1f}  {flag}')
    if errors:
        print(f'  Extreme MAE: {sum(errors)/len(errors):.1f}')

    # ── Feature importance ────────────────────────────────────────────────────
    if hasattr(model, 'feature_importances_'):
        imps = sorted(zip(FEATURE_COLS, model.feature_importances_), key=lambda x: -x[1])
        print('\nTop-15 features by importance:')
        for name, imp in imps[:15]:
            print(f'  {name:<30} {imp:.4f}  {"█" * int(imp * 300)}')

        cats = {
            'PRICE_PCT':   ['pct_btc_price'],
            'MICRO':       [c for c in FEATURE_COLS if c.startswith('pct_') and c != 'pct_btc_price'],
            'BASKET_HOT':  ['qc_hot', 'dy_hot', 'mc_hot'],
            'BASKET_COLD': ['qc_cold', 'dy_cold', 'mc_cold'],
            'DIVERGENCE':  ['dy_vs_qc', 'mc_vs_qc', 'all_spread'],
            'COHERENCE':   ['qc_std', 'dy_std'],
            'EXTREME':     ['count_danger', 'count_safe', 'extreme_bias'],
            'VELOCITY':    ['delta_qc_90d', 'delta_dy_90d', 'delta_mc_90d',
                            'delta_qc_180d', 'delta_dy_180d', 'delta_mc_180d'],
            'PRICE_DIV':   ['price_vs_qc', 'price_vs_dy', 'price_vs_all'],
            'V3_PHASE':    ['w_top', 'w_bot', 'v3_score', 'phase_is_top', 'phase_is_bot',
                            'delta_w_top_30d', 'delta_v3_30d', 'w_top_vs_peak', 'v3_vs_peak',
                            'ath_divergence', 'ath_v3_divergence', 'delta_w_bot_90d'],
        }
        imp_dict = dict(zip(FEATURE_COLS, model.feature_importances_))
        print('\nCategory importance:')
        for cat, cols in cats.items():
            total = sum(imp_dict.get(c, 0) for c in cols)
            print(f'  {cat:<13} {total:.4f}  {"█" * int(total * 200)}')

    # ── Permutation importance ────────────────────────────────────────────────
    try:
        from sklearn.inspection import permutation_importance
        pi = permutation_importance(model, X_te, y_te, n_repeats=10,
                                    scoring='neg_mean_absolute_error', random_state=42)
        cats_perm = {
            'PRICE_PCT':   [FEATURE_COLS.index('pct_btc_price')],
            'MICRO':       [FEATURE_COLS.index(c) for c in FEATURE_COLS if c.startswith('pct_') and c != 'pct_btc_price'],
            'BASKET_HOT':  [FEATURE_COLS.index(c) for c in ['qc_hot', 'dy_hot', 'mc_hot']],
            'BASKET_COLD': [FEATURE_COLS.index(c) for c in ['qc_cold', 'dy_cold', 'mc_cold']],
            'DIVERGENCE':  [FEATURE_COLS.index(c) for c in ['dy_vs_qc', 'mc_vs_qc', 'all_spread']],
            'COHERENCE':   [FEATURE_COLS.index(c) for c in ['qc_std', 'dy_std']],
            'EXTREME':     [FEATURE_COLS.index(c) for c in ['count_danger', 'count_safe', 'extreme_bias']],
            'VELOCITY':    [FEATURE_COLS.index(c) for c in ['delta_qc_90d', 'delta_dy_90d', 'delta_mc_90d',
                                                              'delta_qc_180d', 'delta_dy_180d', 'delta_mc_180d']],
            'PRICE_DIV':   [FEATURE_COLS.index(c) for c in ['price_vs_qc', 'price_vs_dy', 'price_vs_all']],
            'V3_PHASE':    [FEATURE_COLS.index(c) for c in ['w_top', 'w_bot', 'v3_score', 'phase_is_top', 'phase_is_bot',
                                                              'delta_w_top_30d', 'delta_v3_30d', 'w_top_vs_peak', 'v3_vs_peak',
                                                              'ath_divergence', 'ath_v3_divergence', 'delta_w_bot_90d']],
        }
        print('\nPermutation importance (MAE increase when shuffled):')
        for cat, idxs in cats_perm.items():
            total = sum(-pi.importances_mean[i] for i in idxs)
            print(f'  {cat:<13} {total:+.2f}  {"█" * max(0, int(total * 100))}')
    except Exception:
        pass

    # ── Today diagnostic ──────────────────────────────────────────────────────
    import datetime
    today = datetime.date.today().isoformat()
    if today in feat_by_date:
        row = feat_by_date[today]
        feat = [float(row[c]) if row.get(c) is not None else float('nan') for c in FEATURE_COLS]
        feat_imp = [v if not (isinstance(v, float) and math.isnan(v)) else col_medians[i]
                    for i, v in enumerate(feat)]
        pred = float(model.predict(np.array([feat_imp], dtype=np.float32))[0])
        print(f'\nToday ({today}): V5={pred:.1f}')

    # ── SHAP via XGBoost native pred_contribs ────────────────────────────────
    try:
        import xgboost as xgb
        dmat_te5 = xgb.DMatrix(X_te[:5])
        shap_vals = model.get_booster().predict(dmat_te5, pred_contribs=True)
        # shape: (n_samples, n_features+1) — last col is bias term
        mean_abs = np.abs(shap_vals[:, :-1]).mean(axis=0)
        top5_idx = np.argsort(mean_abs)[-5:][::-1]
        print('\nSHAP top-5 (mean |contribution| on test):')
        for i in top5_idx:
            print(f'  {FEATURE_COLS[i]:<30} {mean_abs[i]:.3f}')
    except Exception as e:
        print(f'\nSHAP failed: {e}')

    # ── Save ─────────────────────────────────────────────────────────────────
    with open('data/training_features.json') as f:
        all_rows_fresh = json.load(f)
    metric_history = build_metric_history(all_rows_fresh)
    payload = {
        'model':           model,
        'feature_cols':    FEATURE_COLS,
        'metric_history':  metric_history,
        'col_medians':     col_medians.tolist(),
        'model_type':      model_type,
        'trained_on':      len(X_tr),
        'test_rmse':       round(rmse, 2),
        'test_mae':        round(mae, 2),
        'version':         'v5.17',
    }
    out = Path('data/v5_mixing_model.pkl')
    with open(out, 'wb') as f:
        pickle.dump(payload, f)
    print(f'\nSaved → {out}  (v5.17, {len(FEATURE_COLS)} features)')


if __name__ == '__main__':
    main()
