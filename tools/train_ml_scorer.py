"""Train Random Forest ML scorer on historical metric data.

Features: 12 metric scores [0-100] at a given date.
Label:    1 if 90-day forward BTC return > 0, else 0.

Key property: RF captures metric interactions naturally.
  CB=0 + NUPL=47 (tech panic mid-cycle) → different prediction than
  CB=0 + NUPL=15 (tech panic at genuine bottom)

Run after build_ml_training_data.py:
  python3 tools/train_ml_scorer.py
Output: data/ml_scorer.pkl
"""
import sys, os, json, pickle, datetime
sys.path.insert(0, '.')

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit, cross_validate
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

ROOT = os.path.join(os.path.dirname(__file__), '..')


def main():
    path_data = os.path.join(ROOT, 'data', 'ml_training_data.json')
    if not os.path.exists(path_data):
        print(f"ERROR: {path_data} not found — run build_ml_training_data.py first")
        sys.exit(1)

    with open(path_data) as f:
        data = json.load(f)

    feature_keys = data['feature_keys']
    records = sorted(data['records'], key=lambda r: r['date'])

    X_raw, y = [], []
    for r in records:
        row = [r['features'].get(k) for k in feature_keys]
        row = [v if v is not None else np.nan for v in row]
        X_raw.append(row)
        y.append(r['label'])

    X = np.array(X_raw, dtype=float)
    y = np.array(y, dtype=int)

    print(f"Dataset: {len(y)} samples × {X.shape[1]} features")
    print(f"Labels:  {y.sum()} positive ({100*y.mean():.1f}%), {(1-y).sum()} negative")

    clf = Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('rf', RandomForestClassifier(
            n_estimators=500,
            max_depth=8,
            min_samples_leaf=15,
            class_weight='balanced',
            random_state=42,
            n_jobs=-1,
        )),
    ])

    # 3-fold: Bitcoin's 4-year cycle means we need ≥2 years to train meaningfully.
    # 5-fold on 8 years gives fold-1 only 1.6 years → near-random performance there.
    tscv = TimeSeriesSplit(n_splits=3)
    cv = cross_validate(clf, X, y, cv=tscv,
                        scoring=['roc_auc', 'accuracy'],
                        return_train_score=True)

    print(f"\nTimeSeriesSplit CV (3 folds):")
    print(f"  ROC-AUC train: {cv['train_roc_auc'].mean():.3f} ± {cv['train_roc_auc'].std():.3f}")
    print(f"  ROC-AUC  test: {cv['test_roc_auc'].mean():.3f} ± {cv['test_roc_auc'].std():.3f}")
    print(f"  Accuracy test: {cv['test_accuracy'].mean():.3f} ± {cv['test_accuracy'].std():.3f}")
    print(f"  Per-fold AUC: {[round(s,3) for s in cv['test_roc_auc']]}")

    clf.fit(X, y)

    rf = clf.named_steps['rf']
    print(f"\nFeature importances (out-of-bag learned weights):")
    pairs = sorted(zip(feature_keys, rf.feature_importances_), key=lambda x: -x[1])
    for k, imp in pairs:
        bar = '█' * int(imp * 50)
        print(f"  {k:<22} {imp:.3f}  {bar}")

    model_data = {
        'pipeline':     clf,
        'feature_keys': feature_keys,
        'n_samples':    int(len(y)),
        'cv_roc_auc':   float(cv['test_roc_auc'].mean()),
        'trained_at':   datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    path_model = os.path.join(ROOT, 'data', 'ml_scorer.pkl')
    with open(path_model, 'wb') as f:
        pickle.dump(model_data, f)

    print(f"\nSaved → {path_model}")
    print(f"  ROC-AUC: {model_data['cv_roc_auc']:.3f}  |  samples: {model_data['n_samples']}")


if __name__ == '__main__':
    main()
