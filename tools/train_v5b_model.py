#!/usr/bin/env python3
"""Train V5B: Forward Risk Model.

Input:   data/training_features_b.json  (built by build_v5b_labels.py)
Output:  data/v5b_model.pkl

V5B answers a different question from V3/V5A:
  NOT: "Where are we in the cycle?" (cycle position)
  BUT: "How risky is it to hold BTC right now?" (future downside)

Label: max_drawdown_365d — maximum price drop (%) over the next 365 days.
  0%   = perfect buy zone (price only goes up)
  75%+ = major sell zone (severe bear market follows)

Uses the same 49 features as V5A so the feature pipeline is unchanged.
"""
import json
import math
import pickle
import numpy as np
from pathlib import Path

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.train_mixing_model import FEATURE_COLS, build_metric_history


def load_data():
    path = Path('data/training_features_b.json')
    rows = json.loads(path.read_text())
    X, y, dates = [], [], []
    for row in rows:
        lb = row.get('label_b')
        if lb is None:
            continue
        feat = [
            float(row[col]) if row.get(col) is not None else float('nan')
            for col in FEATURE_COLS
        ]
        X.append(feat)
        y.append(float(lb))
        dates.append(row['date'])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32), dates


def main():
    print('Loading features...')
    X, y, dates = load_data()
    print(f'  {len(X)} examples  ×  {X.shape[1]} features  (V5B)')
    print(f'  Label range: {y.min():.1f}% – {y.max():.1f}%  mean={y.mean():.1f}%')

    split = int(len(X) * 0.80)
    X_tr, X_te = X[:split], X[split:]
    y_tr, y_te = y[:split], y[split:]
    dates_te = dates[split:]

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
    except ImportError:
        from sklearn.ensemble import GradientBoostingRegressor
        model = GradientBoostingRegressor(
            n_estimators=300, max_depth=4, learning_rate=0.05,
            subsample=0.8, random_state=42,
        )

    model.fit(X_tr, y_tr)

    pred_te = model.predict(X_te)
    residuals = np.abs(pred_te - y_te)
    mae  = float(residuals.mean())
    rmse = float(np.sqrt((residuals ** 2).mean()))

    # Extreme: dates where actual drawdown > 50% (real sell zones) or < 5% (real buy zones)
    mask_ext = (y_te > 50) | (y_te < 5)
    ext_mae = float(residuals[mask_ext].mean()) if mask_ext.any() else float('nan')

    print(f'\n  Test MAE={mae:.2f}%  RMSE={rmse:.2f}%  Extreme MAE={ext_mae:.2f}%')

    # Feature importance (top 10)
    if hasattr(model, 'feature_importances_'):
        imp = list(zip(FEATURE_COLS, model.feature_importances_))
        imp.sort(key=lambda x: -x[1])
        print('\n  Top-10 feature importance:')
        for name, val in imp[:10]:
            print(f'    {val:.4f}  {name}')

    # Validate on key cycle dates
    print('\n  Key date validation (V5B = expected max drawdown %):')
    rows_all = json.loads(Path('data/training_features_b.json').read_text())
    by_date = {r['date'][:10]: r for r in rows_all}
    check = [
        ('2018-12-15', 'Dec 2018 bottom'),
        ('2019-06-26', 'Jun 2019 local top'),
        ('2020-03-12', 'COVID crash'),
        ('2021-04-14', 'Apr 2021 top'),
        ('2021-11-10', 'Nov 2021 ATH'),
        ('2022-06-18', 'Jun 2022 capitulation'),
        ('2022-11-21', 'FTX bottom'),
        ('2023-10-23', 'pre-rally 2024'),
        ('2024-11-15', 'pre-ATH 2025'),
    ]
    for date, label in check:
        row = by_date.get(date)
        if row is None:
            print(f'  {date}  {label:26s}  NO DATA')
            continue
        feat = np.array([[
            float(row[col]) if row.get(col) is not None else float('nan')
            for col in FEATURE_COLS
        ]], dtype=np.float32)
        for j in range(feat.shape[1]):
            if np.isnan(feat[0, j]):
                feat[0, j] = col_medians[j]
        pred = float(model.predict(feat)[0])
        actual = row.get('label_b')
        print(f'  {date}  {label:26s}  actual={actual:.1f}%  V5B={pred:.1f}%')

    # Save model
    out = {
        'model':        model,
        'feature_cols': FEATURE_COLS,
        'col_medians':  col_medians,
        'metric_history': build_metric_history(rows_all),
        'version':      'v5b.1',
        'label':        'max_drawdown_365d',
    }
    out_path = Path('data/v5b_model.pkl')
    with open(out_path, 'wb') as f:
        pickle.dump(out, f)
    print(f'\n  Saved → {out_path}')


if __name__ == '__main__':
    main()
