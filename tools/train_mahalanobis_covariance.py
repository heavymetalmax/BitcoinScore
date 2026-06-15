#!/usr/bin/env python3
import sys
import os
import json
import math
import datetime

sys.path.insert(0, '.')

from tools.backtest import load_data
from tools.optimize_v3_relevance import get_btc_price_dict, build_precomputed_dataset

def calculate_covariance():
    print("=== TRAINING MAHALANOBIS COVARIANCE MATRIX ===")
    series = load_data()
    btc_price = get_btc_price_dict()

    # Load scores history for TiZ
    scores_history = None
    scores_path = 'data/history/scores.json'
    if os.path.exists(scores_path):
        try:
            with open(scores_path, encoding='utf-8') as f:
                history_data = json.load(f)
                scores_history = [(r['date'], r.get('final_score')) for r in history_data if r.get('date')]
        except Exception:
            pass

    dataset = build_precomputed_dataset(series, btc_price, scores_history)
    if not dataset:
        print("Error: Empty dataset.")
        return

    oc_keys = ['nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'asopr']

    # Filter rows where all 5 metrics are present
    valid_rows = []
    for row in dataset:
        norm = row['normalized']
        if all(norm.get(k) is not None for k in oc_keys):
            valid_rows.append([norm[k] / 100.0 for k in oc_keys])

    print(f"Total valid history rows with all 5 on-chain metrics: {len(valid_rows)}")
    if len(valid_rows) < 10:
        print("Error: Not enough data points.")
        return

    # Calculate means
    n = len(valid_rows)
    m = len(oc_keys)
    means = [sum(row[j] for row in valid_rows) / n for j in range(m)]

    # Calculate covariance matrix
    cov = [[0.0] * m for _ in range(m)]
    for row in valid_rows:
        for i in range(m):
            for j in range(m):
                cov[i][j] += (row[i] - means[i]) * (row[j] - means[j])

    for i in range(m):
        for j in range(m):
            cov[i][j] /= (n - 1)

    print("\nCalculated Covariance Matrix for On-Chain Metrics:")
    for i in range(m):
        row_str = "  ".join(f"{cov[i][j]:.6f}" for j in range(m))
        print(f"  [{oc_keys[i]:<12}] -> {row_str}")

    # Save to data/mahalanobis_covariance.json
    out_path = 'data/mahalanobis_covariance.json'
    output_data = {
        'keys': oc_keys,
        'means': means,
        'covariance': cov
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2)

    print(f"\nSaved covariance data to: {out_path}")

if __name__ == '__main__':
    calculate_covariance()
