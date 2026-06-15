#!/usr/bin/env python3
import sys
import os
import json
import datetime

sys.path.insert(0, '.')

from tools.backtest import load_data
from tools.optimize_v3_relevance import (
    get_btc_price_dict,
    build_precomputed_dataset,
    compute_trader_loss,
    optimize_relevance_weights,
    fast_evaluate_score
)
from scraper.utility_evaluator import RELEVANCE_PROFILES

def run_walk_forward():
    print("=== STARTING WALK-FORWARD VALIDATION (EXPERIMENT 1) ===")
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

    # Build the full precomputed dataset
    dataset = build_precomputed_dataset(series, btc_price, scores_history)
    if not dataset:
        print("Error: Empty dataset.")
        return

    # Define Walk-Forward Folds: (train_start, train_end, test_start, test_end)
    folds = [
        {
            'name': 'Fold 1 (Train: 2018-2021, Test: 2022-2023.5)',
            'train_start': '2018-02-01', 'train_end': '2021-12-31',
            'test_start': '2022-01-01', 'test_end': '2023-06-30'
        },
        {
            'name': 'Fold 2 (Train: 2018-2023.5, Test: 2023.5-2024)',
            'train_start': '2018-02-01', 'train_end': '2023-06-30',
            'test_start': '2023-07-01', 'test_end': '2024-12-31'
        },
        {
            'name': 'Fold 3 (Train: 2018-2024, Test: 2025)',
            'train_start': '2018-02-01', 'train_end': '2024-12-31',
            'test_start': '2025-01-01', 'test_end': '2025-12-17'
        }
    ]

    results = []

    for idx, f in enumerate(folds):
        print(f"\n--- Running {f['name']} ---")
        
        # Split datasets
        train_data = [r for r in dataset if f['train_start'] <= r['date'] <= f['train_end']]
        test_data = [r for r in dataset if f['test_start'] <= r['date'] <= f['test_end']]
        
        print(f"  Training samples: {len(train_data)} | Testing samples: {len(test_data)}")
        
        # 1. Evaluate baseline profiles on Test Set (OOS Baseline)
        base_test_loss, base_test_ret, base_test_dd, base_test_mse, base_test_hinge = compute_trader_loss(test_data, RELEVANCE_PROFILES)
        
        # 2. Optimize profiles on Train Set
        print(f"  Optimizing profiles on training set...")
        opt_profiles, _ = optimize_relevance_weights(train_data, RELEVANCE_PROFILES)
        
        # 3. Evaluate optimized profiles on Train Set (In-Sample Optimized)
        opt_train_loss, opt_train_ret, opt_train_dd, opt_train_mse, opt_train_hinge = compute_trader_loss(train_data, opt_profiles)
        
        # 4. Evaluate optimized profiles on Test Set (Out-of-Sample Optimized)
        opt_test_loss, opt_test_ret, opt_test_dd, opt_test_mse, opt_test_hinge = compute_trader_loss(test_data, opt_profiles)
        
        results.append({
            'fold': f['name'],
            'baseline_test': {
                'loss': base_test_loss, 'ret': base_test_ret, 'dd': base_test_dd, 'mse': base_test_mse, 'hinge': base_test_hinge
            },
            'opt_train': {
                'loss': opt_train_loss, 'ret': opt_train_ret, 'dd': opt_train_dd, 'mse': opt_train_mse, 'hinge': opt_train_hinge
            },
            'opt_test': {
                'loss': opt_test_loss, 'ret': opt_test_ret, 'dd': opt_test_dd, 'mse': opt_test_mse, 'hinge': opt_test_hinge
            }
        })

    # Summary Output
    print("\n========================================================")
    print("WALK-FORWARD VALIDATION SUMMARY")
    print("========================================================")
    for res in results:
        print(f"\n{res['fold']}:")
        print("  Baseline (OOS - Unoptimized Profiles):")
        print(f"    Loss: {res['baseline_test']['loss']:.2f} | Return: {res['baseline_test']['ret']:.1f}% | Max DD: {res['baseline_test']['dd']*100:.1f}% | MSE: {res['baseline_test']['mse']:.2f} | Hinge: {res['baseline_test']['hinge']:.2f}")
        
        print("  Optimized (In-Sample - Training Set):")
        print(f"    Loss: {res['opt_train']['loss']:.2f} | Return: {res['opt_train']['ret']:.1f}% | Max DD: {res['opt_train']['dd']*100:.1f}% | MSE: {res['opt_train']['mse']:.2f} | Hinge: {res['opt_train']['hinge']:.2f}")
        
        print("  Optimized (Out-of-Sample - Testing Set):")
        print(f"    Loss: {res['opt_test']['loss']:.2f} | Return: {res['opt_test']['ret']:.1f}% | Max DD: {res['opt_test']['dd']*100:.1f}% | MSE: {res['opt_test']['mse']:.2f} | Hinge: {res['opt_test']['hinge']:.2f}")
        
        # Improvement check
        loss_diff = res['baseline_test']['loss'] - res['opt_test']['loss']
        ret_diff = res['opt_test']['ret'] - res['baseline_test']['ret']
        print(f"  OOS Performance Diff (Optimized vs Baseline):")
        print(f"    Loss Change: {loss_diff:+.2f} (lower is better) | ROI Change: {ret_diff:+.1f}% (higher is better)")

if __name__ == '__main__':
    run_walk_forward()
