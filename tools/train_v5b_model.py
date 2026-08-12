#!/usr/bin/env python3
"""Train V5B with purged walk-forward validation.

The 365-day forward label makes adjacent rows overlap heavily. Every validation
fold therefore removes the 365 days immediately preceding its test window from
training. Metrics are compared with constant mean/median baselines, and the
final production model is fit only after out-of-sample diagnostics complete.
"""
import datetime
import hashlib
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.train_mixing_model import FEATURE_COLS, build_metric_history

PURGE_DAYS = 365
FOLD_START_FRACTIONS = (0.55, 0.70, 0.85)

ABLATION_GROUPS = {
    'without_price_position': {
        'pct_btc_price', 'price_vs_qc', 'price_vs_dy', 'price_vs_all',
        'ath_divergence', 'ath_v3_divergence',
    },
    'without_phase_context': {
        'w_top', 'w_bot', 'v3_score', 'phase_is_top', 'phase_is_bot',
        'delta_w_bot_90d', 'delta_w_top_30d', 'delta_v3_30d',
        'w_top_vs_peak', 'v3_vs_peak', 'ath_divergence', 'ath_v3_divergence',
    },
}


def load_data():
    path = Path('data/training_features_b.json')
    rows = json.loads(path.read_text())
    packed = []
    for row in rows:
        if row.get('label_b') is None:
            continue
        packed.append((
            row['date'][:10],
            [float(row[c]) if row.get(c) is not None else float('nan') for c in FEATURE_COLS],
            float(row['label_b']),
            row,
        ))
    packed.sort(key=lambda x: x[0])
    dates = [x[0] for x in packed]
    X = np.array([x[1] for x in packed], dtype=np.float32)
    y = np.array([x[2] for x in packed], dtype=np.float32)
    return X, y, dates, [x[3] for x in packed], rows


def make_model():
    try:
        import xgboost as xgb
        return xgb.XGBRegressor(
            n_estimators=400, max_depth=5, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.75, min_child_weight=8,
            reg_lambda=2.0, random_state=42, tree_method='hist', verbosity=0,
        )
    except ImportError:
        from sklearn.ensemble import GradientBoostingRegressor
        return GradientBoostingRegressor(
            n_estimators=300, max_depth=4, learning_rate=0.05,
            subsample=0.8, random_state=42,
        )


def fit_imputer(X_train):
    medians = np.nanmedian(X_train, axis=0)
    return np.where(np.isnan(medians), 0.5, medians)


def impute(X, medians):
    return np.where(np.isnan(X), medians, X)


def regression_metrics(y_true, pred, train_y):
    residuals = np.abs(pred - y_true)
    mean_pred = np.full_like(y_true, float(np.mean(train_y)))
    median_pred = np.full_like(y_true, float(np.median(train_y)))
    extreme = (y_true > 50) | (y_true < 5)
    return {
        'mae': round(float(residuals.mean()), 4),
        'rmse': round(float(np.sqrt(np.mean((pred - y_true) ** 2))), 4),
        'extreme_mae': round(float(residuals[extreme].mean()), 4) if extreme.any() else None,
        'mean_baseline_mae': round(float(np.mean(np.abs(mean_pred - y_true))), 4),
        'median_baseline_mae': round(float(np.mean(np.abs(median_pred - y_true))), 4),
    }


def make_purged_folds(dates):
    n = len(dates)
    starts = [int(n * f) for f in FOLD_START_FRACTIONS]
    ends = starts[1:] + [n]
    folds = []
    for fold_no, (start, end) in enumerate(zip(starts, ends), 1):
        test_start = datetime.date.fromisoformat(dates[start])
        train_cutoff = (test_start - datetime.timedelta(days=PURGE_DAYS)).isoformat()
        train_idx = np.array([i for i, d in enumerate(dates[:start]) if d <= train_cutoff])
        test_idx = np.arange(start, end)
        if len(train_idx) < 365 or len(test_idx) == 0:
            continue
        folds.append((fold_no, train_idx, test_idx, train_cutoff))
    return folds


def walk_forward(X, y, dates, feature_cols):
    selected = [FEATURE_COLS.index(c) for c in feature_cols]
    oof = []
    reports = []
    for fold_no, train_idx, test_idx, cutoff in make_purged_folds(dates):
        X_tr, X_te = X[train_idx][:, selected], X[test_idx][:, selected]
        y_tr, y_te = y[train_idx], y[test_idx]
        medians = fit_imputer(X_tr)
        model = make_model()
        model.fit(impute(X_tr, medians), y_tr)
        pred = model.predict(impute(X_te, medians))
        metrics = regression_metrics(y_te, pred, y_tr)
        reports.append({
            'fold': fold_no,
            'train_start': dates[int(train_idx[0])],
            'train_end': dates[int(train_idx[-1])],
            'purge_cutoff': cutoff,
            'test_start': dates[int(test_idx[0])],
            'test_end': dates[int(test_idx[-1])],
            'train_rows': int(len(train_idx)),
            'test_rows': int(len(test_idx)),
            **metrics,
        })
        oof.extend((dates[int(i)], float(y[int(i)]), float(p)) for i, p in zip(test_idx, pred))
    return reports, oof


def aggregate(reports):
    if not reports:
        return {}
    keys = ('mae', 'rmse', 'mean_baseline_mae', 'median_baseline_mae')
    return {k: round(float(np.mean([r[k] for r in reports])), 4) for k in keys}


def decision_diagnostics(oof, rows_by_date, context_buy=35, outlook_buy=20,
                         context_sell=65, outlook_sell=45):
    counts = {'buy_signals': 0, 'buy_correct': 0, 'sell_signals': 0, 'sell_correct': 0}
    for date, actual, pred in oof:
        context = rows_by_date.get(date, {}).get('v3_score')
        if context is None:
            continue
        if context < context_buy and pred < outlook_buy:
            counts['buy_signals'] += 1
            counts['buy_correct'] += int(actual < outlook_buy)
        if context >= context_sell and pred >= outlook_sell:
            counts['sell_signals'] += 1
            counts['sell_correct'] += int(actual >= outlook_sell)
    counts['buy_precision'] = round(counts['buy_correct'] / counts['buy_signals'], 4) if counts['buy_signals'] else None
    counts['sell_precision'] = round(counts['sell_correct'] / counts['sell_signals'], 4) if counts['sell_signals'] else None
    return counts


def main():
    X, y, dates, labeled_rows, all_rows = load_data()
    if len(X) < 1000:
        raise RuntimeError(f'Insufficient fully labeled rows: {len(X)}')
    print(f'V5B: {len(X)} complete labels × {X.shape[1]} features ({dates[0]} → {dates[-1]})')

    reports, oof = walk_forward(X, y, dates, FEATURE_COLS)
    summary = aggregate(reports)
    if not reports:
        raise RuntimeError('No valid purged walk-forward folds')
    for r in reports:
        print(f"  fold {r['fold']}: train≤{r['train_end']} test={r['test_start']}→{r['test_end']} "
              f"MAE={r['mae']:.2f} mean-baseline={r['mean_baseline_mae']:.2f}")

    ablations = {}
    for name, excluded in ABLATION_GROUPS.items():
        cols = [c for c in FEATURE_COLS if c not in excluded]
        ablation_reports, _ = walk_forward(X, y, dates, cols)
        ablations[name] = {'features': len(cols), **aggregate(ablation_reports)}

    by_date = {r['date'][:10]: r for r in labeled_rows}
    decisions = decision_diagnostics(oof, by_date)
    baseline_mae = min(summary['mean_baseline_mae'], summary['median_baseline_mae'])
    passes_baseline = summary['mae'] < baseline_mae
    validation = {
        'schema_version': 2,
        'label': 'max_drawdown_365d',
        'purge_days': PURGE_DAYS,
        'folds': reports,
        'aggregate': summary,
        'passes_baseline': passes_baseline,
        'ablations': ablations,
        'fixed_threshold_oos': decisions,
        'oof_rows': [{'date': d, 'actual': round(a, 3), 'predicted': round(p, 3)} for d, a, p in oof],
    }
    Path('data/v5b_validation.json').write_text(json.dumps(validation, indent=2))

    medians = fit_imputer(X)
    model = make_model()
    model.fit(impute(X, medians), y)
    source_bytes = Path('data/training_features_b.json').read_bytes()
    package = {
        'model': model,
        'feature_cols': FEATURE_COLS,
        'col_medians': medians,
        'metric_history': build_metric_history(all_rows),
        'version': 'v5b.2-purged-walk-forward',
        'label': 'max_drawdown_365d',
        'metadata': {
            'trained_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'train_start': dates[0], 'train_end': dates[-1], 'train_rows': len(dates),
            'training_sha256': hashlib.sha256(source_bytes).hexdigest(),
            'validation': summary, 'purge_days': PURGE_DAYS,
            'passes_baseline': passes_baseline,
            'version': 'v5b.2-purged-walk-forward',
        },
    }
    with open('data/v5b_model.pkl', 'wb') as f:
        pickle.dump(package, f)
    print(f"OOS aggregate: MAE={summary['mae']:.2f}; mean baseline={summary['mean_baseline_mae']:.2f}")
    print('Saved data/v5b_validation.json and data/v5b_model.pkl')


if __name__ == '__main__':
    main()
