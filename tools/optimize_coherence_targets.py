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
    OC_GROUP,
    TECH_GROUP,
    map_pi_cycle_gap
)
from scraper.utility_evaluator import RELEVANCE_PROFILES

def evaluate_score_with_targets(row, profiles, bottom_target, top_target):
    """Compute V3.1 score with custom dampening targets."""
    normalized = row['normalized']
    w_top = row['w_top']
    w_bot = row['w_bot']
    w_neutral = row['w_neutral']
    phase = row['phase']
    tiz_score = row['tiz_score']
    oc_coherence = row['oc_coherence']
    pi_cross = row['pi_cross']
    
    # 1. Compute dynamic utility weights
    utilities = {}
    for k, s in normalized.items():
        if s is None:
            continue
            
        canonical_key = k
        if k == 'mvrv':
            canonical_key = 'mvrv_z_score'
        elif k == 'puell_multiple':
            canonical_key = 'puell'
        elif k == 'pi_cycle':
            canonical_key = 'pi_gap'
            
        prof = profiles.get(canonical_key)
        if prof:
            utility = (
                w_top * prof.get('TOP', 0.5) +
                w_bot * prof.get('BOTTOM', 0.5) +
                w_neutral * prof.get('NEUTRAL', 0.5)
            )
        else:
            utility = 0.5
            
        utilities[k] = max(0.1, min(1.0, utility))
        
    # 2. Dynamic group averages
    def weighted_avg(group_keys):
        total_uw = 0.0
        total_us = 0.0
        for k in group_keys:
            s = normalized.get(k)
            u = utilities.get(k, 0.5)
            if s is not None:
                total_us += s * u
                total_uw += u
        return round(total_us / total_uw) if total_uw > 0 else None
        
    oc_avg = weighted_avg(OC_GROUP)
    tech_avg = weighted_avg(TECH_GROUP)
    
    # 3. Dynamic blend
    if oc_avg is not None and tech_avg is not None:
        if phase == 'BOTTOM' and tiz_score is not None:
            final = round(0.40 * oc_avg + 0.40 * tech_avg + 0.20 * tiz_score)
        else:
            final = round(0.50 * oc_avg + 0.50 * tech_avg)
    elif oc_avg is not None:
        final = oc_avg
    elif tech_avg is not None:
        final = tech_avg
    else:
        return None
        
    # 4. Custom Coherence dampening targets
    if phase == 'BOTTOM':
        coh_floor = 0.60
        neutral_target = bottom_target
    elif phase == 'TOP':
        coh_floor = 0.55
        neutral_target = top_target
    else:
        coh_floor = 0.45
        neutral_target = 50
        
    coh_factor = round(coh_floor + (1.0 - coh_floor) * oc_coherence, 3)
    final = round(neutral_target + (final - neutral_target) * coh_factor)
    
    # 5. Pi Cycle cross override
    if pi_cross:
        final = max(final, 85)
        
    return final

def compute_trader_loss_with_targets(dataset, profiles, bottom_target, top_target):
    cash = 10000.0
    btc = 0.0
    portfolio_history = []
    
    # Sort dataset chronologically
    sorted_data = sorted(dataset, key=lambda x: x['date'])
    
    buy_thr = 25
    sell_thr = 73
    
    for row in sorted_data:
        score = evaluate_score_with_targets(row, profiles, bottom_target, top_target)
        price = row['price']
        
        if score is not None:
            if score <= buy_thr and cash > 10.0:
                btc = cash / price
                cash = 0.0
            elif score >= sell_thr and btc > 0.0:
                cash = btc * price
                btc = 0.0
                
        val = cash + btc * price
        portfolio_history.append(val)
        
    final_val = cash + btc * sorted_data[-1]['price']
    total_return_pct = (final_val - 10000.0) / 10000.0 * 100
    
    # Max Drawdown
    peak = 10000.0
    max_dd = 0.0
    for v in portfolio_history:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
            
    # MSE
    total_sq_err = 0.0
    total_weight = 0.0
    for row in dataset:
        score = evaluate_score_with_targets(row, profiles, bottom_target, top_target)
        if score is not None:
            weight = 1.0 + 4.0 * ((row['target'] - 50.0) / 50.0) ** 2
            total_sq_err += weight * ((score - row['target']) ** 2)
            total_weight += weight
    mse = total_sq_err / total_weight if total_weight > 0 else 10000.0
    
    # Hinge Loss
    hinge_loss = 0.0
    bottoms = {'2018-12-15', '2020-03-13', '2022-06-18', '2022-11-21'}
    tops = {'2021-04-14', '2021-11-10', '2024-03-14', '2025-01-20', '2025-09-29'}

    for row in dataset:
        d = row['date']
        if d in bottoms or d in tops:
            score = evaluate_score_with_targets(row, profiles, bottom_target, top_target)
            if score is not None:
                if d in bottoms:
                    if score > 15:
                        hinge_loss += 5.0 * (score - 15) ** 2
                elif d in tops:
                    if score < 73:
                        hinge_loss += 5.0 * (73 - score) ** 2
                        
    loss = -1.0 * total_return_pct + 4.0 * (max_dd * 100) + 0.25 * mse + hinge_loss
    return loss, total_return_pct, max_dd, mse, hinge_loss

def run_grid_search():
    print("=== STARTING COHERENCE TARGET GRID SEARCH (EXPERIMENT 4) ===")
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

    # Define Grid bounds
    bottom_targets = range(15, 36) # [15, ..., 35]
    top_targets = range(65, 86)    # [65, ..., 85]

    results = []

    # Run grid search
    total_combinations = len(bottom_targets) * len(top_targets)
    count = 0
    print(f"Grid search space size: {total_combinations} combinations...")

    for bt in bottom_targets:
        for tt in top_targets:
            count += 1
            if count % 50 == 0:
                print(f"  Processed {count}/{total_combinations} combinations...")
                
            loss, ret, dd, mse, hinge = compute_trader_loss_with_targets(dataset, RELEVANCE_PROFILES, bt, tt)
            
            results.append({
                'bottom_target': bt,
                'top_target': tt,
                'loss': loss,
                'return': ret,
                'drawdown': dd,
                'mse': mse,
                'hinge_loss': hinge
            })

    # Sort results by overall loss descending (lower loss is better)
    results.sort(key=lambda x: x['loss'])

    print("\n========================================================")
    print("GRID SEARCH RESULTS: TOP 5 TARGET COMBINATIONS")
    print("========================================================")
    for i, r in enumerate(results[:5]):
        print(f"Rank {i+1}: Bottom Target: {r['bottom_target']} | Top Target: {r['top_target']}")
        print(f"  Trader Loss: {r['loss']:.2f} | Return: {r['return']:.1f}% | Max DD: {r['drawdown']*100:.1f}% | MSE: {r['mse']:.2f} | Hinge Loss: {r['hinge_loss']:.2f}")

    # Best baseline comparison
    # Current is bt=30, tt=70
    current_res = next(r for r in results if r['bottom_target'] == 30 and r['top_target'] == 70)
    print("\nCurrent Baseline (Bottom Target: 30, Top Target: 70):")
    print(f"  Trader Loss: {current_res['loss']:.2f} | Return: {current_res['return']:.1f}% | Max DD: {current_res['drawdown']*100:.1f}% | MSE: {current_res['mse']:.2f} | Hinge Loss: {current_res['hinge_loss']:.2f}")

    best = results[0]
    loss_improvement = current_res['loss'] - best['loss']
    roi_improvement = best['return'] - current_res['return']
    print(f"\nNet Improvement (Best vs Current):")
    print(f"  Loss Improvement: {loss_improvement:+.2f}")
    print(f"  ROI Improvement: {roi_improvement:+.1f}%")

if __name__ == '__main__':
    run_grid_search()
