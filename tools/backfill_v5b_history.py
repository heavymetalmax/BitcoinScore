"""Backfill v5b_score and market_regime into web/score_history.json.

Runs V5B model on all dates in training_features.json (2017-2026),
merges with score_history.json (which has final_score + price),
and writes v5b_score + market_regime to each entry.
"""
import json
import pickle
import sys
import os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.train_mixing_model import FEATURE_COLS


def market_regime(pos, v5b):
    buy  = pos <  35 and v5b <  20
    sell = pos >= 65 and v5b >= 45
    hold = pos >= 65 and v5b <  45
    if buy:  return 'Buy'
    if sell: return 'Sell'
    if hold: return 'Hold'
    return 'Wait'


def main():
    # Load V5B model
    with open('data/v5b_model.pkl', 'rb') as f:
        pkg = pickle.load(f)
    model       = pkg['model']
    col_medians = pkg.get('col_medians')
    feat_cols   = pkg.get('feature_cols', FEATURE_COLS)

    # Load training features (all dates with feature vectors)
    with open('data/training_features.json') as f:
        feat_rows = json.load(f)

    # Run inference on every row (including rows without label)
    v5b_by_date = {}
    for row in feat_rows:
        date = row['date']
        feat = np.array([
            float(row[c]) if row.get(c) is not None else float('nan')
            for c in feat_cols
        ], dtype=np.float32).reshape(1, -1)
        # impute with training medians
        if col_medians is not None:
            for j in range(feat.shape[1]):
                if np.isnan(feat[0, j]):
                    feat[0, j] = float(col_medians[j])
        else:
            feat = np.where(np.isnan(feat), 0.5, feat)
        score = float(model.predict(feat)[0])
        v5b_by_date[date] = round(max(0.0, min(100.0, score)), 1)

    # Load score_history.json
    with open('web/score_history.json') as f:
        history = json.load(f)

    # Merge
    updated = 0
    for entry in history:
        date = entry.get('date')
        v5b  = v5b_by_date.get(date)
        if v5b is not None:
            entry['v5b_score'] = v5b
            pos = entry.get('score')
            if pos is not None:
                entry['market_regime'] = market_regime(pos, v5b)
            updated += 1

    print(f'Updated {updated}/{len(history)} entries with v5b_score + market_regime')

    # Write back
    with open('web/score_history.json', 'w') as f:
        json.dump(history, f, separators=(',', ':'))

    # Quick sanity check
    sample = [e for e in history if e.get('v5b_score') is not None]
    if sample:
        for e in [sample[0], sample[len(sample)//2], sample[-1]]:
            print(f"  {e['date']}  score={e.get('score')}  v5b={e.get('v5b_score')}%  regime={e.get('market_regime')}")


if __name__ == '__main__':
    main()
