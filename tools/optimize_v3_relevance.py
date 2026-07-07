"""Scoring V3.1 Relevance Weights Optimizer.

Calibrates the 39 dynamic utility weights (13 metrics x 3 phase states) to
minimize Mean Squared Error (MSE) against the ideal risk target derived from
6-month forward BTC returns. Uses a pure-Python coordinate descent optimizer.

Primary usage: called automatically from train_v3_hmm_model.py — results are
embedded in data/v3_phase_model.pkl as 'metric_relevance' (single source of truth).

Standalone usage (debug/inspection only):
    python tools/optimize_v3_relevance.py
    → writes data/v3_relevance_weights.json for manual inspection.
    This JSON is NOT read by the scoring engine anymore.
"""

import sys
import os
import json
import math
import datetime

sys.path.insert(0, '.')

from tools.backtest import load_data, compute_at, _prev_scores
from scraper.scoring_v2 import phase_signals, _METRIC_LOOKBACK
from scraper.scoring import _oc_coherence
from scraper.utility_evaluator import RELEVANCE_PROFILES
from scraper.scoring_v3 import OC_GROUP, TECH_GROUP, map_pi_cycle_gap, compute_tiz_causal_v3

# 6-month forward returns window
FORWARD_DAYS = 180
_OUTPUT_PATH = 'data/v3_relevance_weights.json'


def forward_return_to_target(fwd_ret_pct):
    """Map 6m forward return % to an ideal risk target in [0, 100]."""
    # Large positive return -> risk target close to 0 (should buy)
    # Large negative return -> risk target close to 100 (should sell/avoid)
    return max(0.0, min(100.0, 50.0 - fwd_ret_pct * 0.25))


def get_btc_price_dict():
    """Load BTC price dictionary from history files."""
    bp_path = 'data/history/btc_price_history.json'
    btc_price = {}
    if os.path.exists(bp_path):
        bp = json.load(open(bp_path, encoding='utf-8'))
        bp_s = bp.get('series', bp) if isinstance(bp, dict) else bp
        for r in bp_s:
            if isinstance(r, list):
                btc_price[str(r[0])[:10]] = float(r[1])
            elif isinstance(r, dict):
                d = str(r.get('date', ''))[:10]
                v = r.get('close') or r.get('value') or r.get('price')
                if d and v:
                    btc_price[d] = float(v)
    return btc_price


def build_precomputed_dataset(series, btc_price, scores_history):
    """Precompute normalized metrics and phase weights for all historical dates."""
    print("Building precomputed dataset...")
    dataset = []
    
    # We run from 2018-02-01 (Fear & Greed start) up to FORWARD_DAYS ago
    start_date = datetime.date(2018, 2, 1)
    end_date = datetime.date.today() - datetime.timedelta(days=FORWARD_DAYS)
    
    # Find all available dates
    dates = []
    curr = start_date
    while curr <= end_date:
        dates.append(curr)
        curr += datetime.timedelta(days=1)
        
    for i, td in enumerate(dates):
        if i % 100 == 0:
            print(f"  Processing date {td} ({i}/{len(dates)})...")
            
        date_str = td.isoformat()
        
        # 1. Price check
        p0 = btc_price.get(date_str)
        if p0 is None or p0 == 0:
            continue
            
        # Target prices in the next FORWARD_DAYS days
        future_prices = []
        for days_offset in range(1, FORWARD_DAYS + 1):
            future_dt = td + datetime.timedelta(days=days_offset)
            pf = btc_price.get(future_dt.isoformat())
            if pf is not None:
                future_prices.append(pf)
                
        if not future_prices:
            continue
            
        p_min = min(future_prices)
        drawdown_pct = (p_min - p0) / p0 * 100
        
        # Endpoint price exactly FORWARD_DAYS later (or nearest available)
        pf_end = future_prices[-1]
        fwd_ret = (pf_end - p0) / p0 * 100
        
        # Dynamic risk target: use the worst return/drawdown in the 6-month window
        worst_ret = min(fwd_ret, drawdown_pct)
        target_risk = max(0.0, min(100.0, 50.0 - worst_ret * 0.9))
        
        # 2. Get normalized metrics
        try:
            r = compute_at(td, series)
            if not r.get('full_coverage'):
                continue
        except Exception:
            continue
            
        prev = _prev_scores(td, series)
        
        # Reconstruct normalized metrics
        normalized = dict(r['scores'])
        
        # Add Pi Cycle Gap
        pi_raw = r['raw'].get('pi_cycle') or r['raw'].get('pi_cycle_gap_pct') or r['raw'].get('pi_gap')
        normalized['pi_gap'] = map_pi_cycle_gap(pi_raw)
        
        # 3. Detect phase weights
        phases = phase_signals(normalized, prev)
        phase = phases.get('phase', 'NEUTRAL')
        top_signal = phases.get('top_signal', 0.0)
        bot_signal = phases.get('bot_signal', 0.0)
        
        w_top = max(0.0, min(100.0, float(top_signal or 0.0))) / 100.0
        w_bot = max(0.0, min(100.0, float(bot_signal or 0.0))) / 100.0
        
        total_w = w_top + w_bot
        if total_w > 1.0:
            w_top /= total_w
            w_bot /= total_w
            w_neutral = 0.0
        else:
            w_neutral = 1.0 - total_w
            
        # 4. TiZ
        tiz_score = None
        if phase == 'BOTTOM' and scores_history:
            tiz_score, _, _ = compute_tiz_causal_v3(scores_history, td)
            
        oc_coherence = _oc_coherence(normalized)
        
        pi_cross = bool(isinstance(pi_raw, dict) and pi_raw.get('cross'))
        
        dataset.append({
            'date': date_str,
            'price': p0,
            'normalized': normalized,
            'w_top': w_top,
            'w_bot': w_bot,
            'w_neutral': w_neutral,
            'phase': phase,
            'tiz_score': tiz_score,
            'oc_coherence': oc_coherence,
            'pi_cross': pi_cross,
            'target': target_risk,
            'fwd_ret': fwd_ret,
        })
        
    print(f"Dataset built successfully. Aligned dates: {len(dataset)}")
    return dataset


def fast_evaluate_score(row, profiles):
    """Compute V3.1 score for a precomputed row given custom profiles."""
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
            
        # Hardcoded noise factor default (1.0) since we optimize baseline weights
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
        
    # 4. Coherence dampening
    if phase == 'BOTTOM':
        coh_floor = 0.60
        neutral_target = 30
    elif phase == 'TOP':
        coh_floor = 0.55
        neutral_target = 70
    else:
        coh_floor = 0.45
        neutral_target = 50
        
    coh_factor = round(coh_floor + (1.0 - coh_floor) * oc_coherence, 3)
    final = round(neutral_target + (final - neutral_target) * coh_factor)
    
    # 5. Pi Cycle cross override
    if pi_cross:
        final = max(final, 85)
        
    return final


def get_param_bounds(key, state):
    # Enforce logical financial constraints as hard bounds.
    # Bounds must NOT clip validated Python defaults in utility_evaluator.py.
    if state == 'BOTTOM':
        # Core on-chain: dominant at bottoms (nupl<0, mvrv<0, cvdd<1, puell<0.5)
        if key in {'nupl', 'mvrv_z_score', 'cvdd_ratio', 'puell'}:
            return 0.70, 1.0
        # rhodl: valid bottom indicator but not primary
        if key == 'rhodl_ratio':
            return 0.30, 0.80
        # fear_greed: extreme fear (score ~5-10) IS a strong bottom signal — must allow high weight
        if key == 'fear_greed':
            return 0.50, 1.0
        # asopr: Fisher sep=0.143 (bottom/top means differ by <1pt) — treat as noise
        if key == 'asopr':
            return 0.05, 0.20
        # Tech/price oscillators: secondary at bottoms but not negligible
        if key in {'cipherb', 'mayer_multiple'}:
            return 0.20, 0.70
        # Macro: low relevance at bottoms (lagging, inverted logic)
        if key in {'m2_yoy', 'yield_curve_spread', 'etf_flows'}:
            return 0.05, 0.40
        # pi_gap: top-focused indicator, very low weight at bottoms
        if key == 'pi_gap':
            return 0.05, 0.20

    elif state == 'NEUTRAL':
        # asopr: noise in all phases — cap across the board
        if key == 'asopr':
            return 0.05, 0.20

    elif state == 'TOP':
        # Strong top signals
        if key in {'cipherb', 'mayer_multiple', 'fear_greed', 'pi_gap', 'nupl', 'mvrv_z_score', 'rhodl_ratio'}:
            return 0.70, 1.0
        # Bottom-focused indicators: low weight at tops
        if key in {'cvdd_ratio', 'puell'}:
            return 0.05, 0.30
        # asopr: noise metric — cap at 0.20 in all phases
        if key == 'asopr':
            return 0.05, 0.20
        # m2_yoy: inverted (high M2 = low score = not a top signal) — cap weight at tops
        if key == 'm2_yoy':
            return 0.05, 0.30

    return 0.1, 1.0


def compute_trader_loss(dataset, profiles, prior_profiles=None, l2_lambda=50.0):
    # 1. Run trading simulation
    cash = 10000.0
    btc = 0.0
    portfolio_history = []
    
    # Sort dataset chronologically
    sorted_data = sorted(dataset, key=lambda x: x['date'])
    
    buy_thr = 25
    sell_thr = 73
    
    for row in sorted_data:
        score = fast_evaluate_score(row, profiles)
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
            
    # 2. Compute MSE
    total_sq_err = 0.0
    total_weight = 0.0
    for row in dataset:
        score = fast_evaluate_score(row, profiles)
        if score is not None:
            weight = 1.0 + 4.0 * ((row['target'] - 50.0) / 50.0) ** 2
            total_sq_err += weight * ((score - row['target']) ** 2)
            total_weight += weight
    mse = total_sq_err / total_weight if total_weight > 0 else 10000.0
    
    # 3. Compute reverse-engineered hinge loss for successful trades
    hinge_loss = 0.0
    bottoms = {'2018-12-15', '2020-03-13', '2022-06-18', '2022-11-21'}
    tops = {'2021-04-14', '2021-11-10', '2024-03-14', '2025-01-20', '2025-09-29'}

    for row in dataset:
        d = row['date']
        if d in bottoms or d in tops:
            score = fast_evaluate_score(row, profiles)
            if score is not None:
                if d in bottoms:
                    if score > 15:
                        hinge_loss += 5.0 * (score - 15) ** 2
                elif d in tops:
                    if score < 73:
                        hinge_loss += 5.0 * (73 - score) ** 2
                        
    # Loss = -1.0 * total_return_pct + 4.0 * (max_dd * 100) + 0.25 * mse + hinge_loss
    loss = -1.0 * total_return_pct + 4.0 * (max_dd * 100) + 0.25 * mse + hinge_loss

    # L2 regularization: penalize deviation from prior weights to reduce overfitting
    if prior_profiles is not None:
        l2 = 0.0
        for key in profiles:
            if key in prior_profiles:
                for state in profiles[key]:
                    if state in prior_profiles[key]:
                        diff = profiles[key][state] - prior_profiles[key][state]
                        l2 += diff * diff
        loss += l2_lambda * l2

    return loss, total_return_pct, max_dd, mse, hinge_loss


def optimize_relevance_weights(dataset, initial_profiles, l2_lambda=50.0):
    """Run pure-Python coordinate descent to optimize weights for trader loss.

    l2_lambda controls regularization strength toward initial_profiles.
    Higher values keep weights closer to their prior (reduces overfitting).
    """
    profiles = {k: dict(v) for k, v in initial_profiles.items()}
    prior    = {k: dict(v) for k, v in initial_profiles.items()}

    keys   = list(profiles.keys())
    states = ['BOTTOM', 'NEUTRAL', 'TOP']

    delta  = 0.05
    epochs = 15

    best_loss, ret_pct, max_dd, mse, hinge_loss = compute_trader_loss(
        dataset, profiles, prior_profiles=prior, l2_lambda=l2_lambda)
    print(f"Initial Baseline Trader Loss: {best_loss:.2f} (Return: {ret_pct:.1f}%, Max DD: {max_dd*100:.1f}%, MSE: {mse:.2f}, Hinge: {hinge_loss:.2f})")

    for epoch in range(1, epochs + 1):
        improved = False
        print(f"Epoch {epoch}/{epochs}...")

        for key in keys:
            for state in states:
                curr_val = profiles[key][state]
                min_b, max_b = get_param_bounds(key, state)

                curr_val = max(min_b, min(max_b, curr_val))
                profiles[key][state] = curr_val

                # Try adding delta
                new_val_up = min(max_b, curr_val + delta)
                if new_val_up != curr_val:
                    profiles[key][state] = new_val_up
                    loss_up, _, _, _, _ = compute_trader_loss(
                        dataset, profiles, prior_profiles=prior, l2_lambda=l2_lambda)
                    if loss_up < best_loss:
                        best_loss = loss_up
                        improved = True
                        curr_val = new_val_up
                        continue

                # Try subtracting delta
                new_val_down = max(min_b, curr_val - delta)
                if new_val_down != curr_val:
                    profiles[key][state] = new_val_down
                    loss_down, _, _, _, _ = compute_trader_loss(
                        dataset, profiles, prior_profiles=prior, l2_lambda=l2_lambda)
                    if loss_down < best_loss:
                        best_loss = loss_down
                        improved = True
                        curr_val = new_val_down
                        continue

                profiles[key][state] = curr_val

        _, ret_pct, max_dd, mse, hinge_loss = compute_trader_loss(
            dataset, profiles, prior_profiles=prior, l2_lambda=l2_lambda)
        print(f"  Current Best Trader Loss: {best_loss:.2f} (Return: {ret_pct:.1f}%, Max DD: {max_dd*100:.1f}%, MSE: {mse:.2f}, Hinge: {hinge_loss:.2f})")
        if not improved:
            print("  Optimizer converged.")
            break

    return profiles, best_loss


def main():
    # Load raw data series
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
            
    # Build precomputed dataset
    dataset = build_precomputed_dataset(series, btc_price, scores_history)
    if not dataset:
        print("Error: Empty dataset.")
        return
        
    initial_profiles = RELEVANCE_PROFILES
    
    # Optimize
    optimized_profiles, final_mse = optimize_relevance_weights(dataset, initial_profiles)
    
    # Save optimized profiles
    os.makedirs(os.path.dirname(_OUTPUT_PATH), exist_ok=True)
    with open(_OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(optimized_profiles, f, indent=2)
        
    print(f"\nOptimization completed successfully!")
    print(f"Optimized weights written to: {_OUTPUT_PATH}")
    print(f"Final Calibrated MSE: {final_mse:.2f}")
    
    # Print comparison of profiles
    print("\n--- Calibration Weight Comparison (Baseline vs Optimized) ---")
    print(f"{'Metric':<20} | {'BOTTOM':<14} | {'NEUTRAL':<14} | {'TOP':<14}")
    print("-" * 70)
    for k in sorted(initial_profiles.keys()):
        init = initial_profiles[k]
        opt = optimized_profiles[k]
        print(f"{k:<20} | "
              f"B:{init['BOTTOM']:.2f}->{opt['BOTTOM']:.2f} | "
              f"N:{init['NEUTRAL']:.2f}->{opt['NEUTRAL']:.2f} | "
              f"T:{init['TOP']:.2f}->{opt['TOP']:.2f}")


if __name__ == '__main__':
    main()
