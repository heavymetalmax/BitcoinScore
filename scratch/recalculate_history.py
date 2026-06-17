#!/usr/bin/env python3
import sys
import os
import json
import datetime

sys.path.insert(0, '.')

from tools.backtest import load_data, compute_at, _prev_scores
from scraper.scoring_v3 import compute_scores_v3

def main():
    print("Loading raw metrics history...")
    series = load_data()

    scores_path = 'data/history/scores.json'
    if not os.path.exists(scores_path):
        print(f"Error: {scores_path} not found.")
        return

    print("Loading existing scores.json...")
    with open(scores_path, 'r', encoding='utf-8') as f:
        entries = json.load(f)

    # Sort chronologically to compute TiZ correctly
    entries.sort(key=lambda x: x.get('date', ''))

    prices = {x['date']: x.get('btc_price') or x.get('price') for x in entries if x.get('date')}

    print(f"Recalculating {len(entries)} historical entries using V3.3 logic...")

    recalculated_history = []
    updated_entries = []

    for i, entry in enumerate(entries):
        date_str = entry.get('date')
        if not date_str:
            continue

        td = datetime.date.fromisoformat(date_str)

        # 1. Fetch raw metrics for this date
        r = compute_at(td, series)
        
        # 2. Get lookback scores
        prev = _prev_scores(td, series)

        # 3. Format raw dictionary for V3
        raw = {
            'nupl':               r['raw'].get('nupl'),
            'mvrv':               r['raw'].get('mvrv'),
            'rhodl_ratio':        r['raw'].get('rhodl_ratio'),
            'cvdd_ratio':         r['raw'].get('cvdd_ratio'),
            'asopr':              r['raw'].get('asopr'),
            'puell_multiple':     r['raw'].get('puell_multiple'),
            'mayer_multiple':     r['raw'].get('mayer_multiple'),
            'fear_greed':         r['raw'].get('fear_greed'),
            'm2_yoy':             r['raw'].get('m2_yoy'),
            'yield_curve_spread': r['raw'].get('yield_curve_spread'),
            'cipherb':            r['raw'].get('cipherb'),
            'etf_flows':          r['raw'].get('etf_flows'),
        }

        # 4. Compute V3.3 score using recalculated history in memory (prevents feedback loops)
        try:
            # Prepare scores_history tuple format for compute_scores_v3
            v3_out = compute_scores_v3(raw, target_date=td, prev_scores=prev, scores_history=recalculated_history)
            
            # Prepare scores_history dict format for orchestrate
            hist_dicts = [
                {'date': x[0], 'final_score': x[1], 'btc_price': prices.get(x[0])}
                for x in recalculated_history
            ]
            
            # Run Orchestrator
            from scraper.orchestrator import orchestrate
            v3_sig = orchestrate(
                v2_score         = v3_out['final_score'],
                v2_oc_coherence  = v3_out.get('oc_coherence', 1.0),
                wr_score         = None,
                wr_coherence     = 0,
                tiz_maturity     = v3_out.get('tiz_maturity'),
                top_signal       = v3_out.get('top_signal'),
                bot_signal       = v3_out.get('bot_signal'),
                is_v3            = True,
                btc_price        = entry.get('btc_price') or entry.get('price'),
                target_date      = td,
                scores_history   = hist_dicts
            )
            
            entry['final_score'] = v3_sig['meta_score']
            entry['onchain_score'] = v3_out['onchain_avg']
            entry['tech_score'] = v3_out['tech_avg']
            entry['phase'] = v3_out['phase']
            entry['w_bot'] = v3_out['w_bot']
            entry['bear_div'] = v3_sig.get('bear_div', False)
            entry['bull_div'] = v3_sig.get('bull_div', False)
            
            # Print milestone updates for progress tracking
            if i % 300 == 0 or date_str in ["2018-12-15", "2021-04-14", "2022-11-21", "2025-07-17", "2025-09-29", "2026-06-12"]:
                print(f"  [{date_str}] V3.3 Score recalculated: {v3_sig['meta_score']} (Phase: {v3_out['phase']}, BearDiv: {entry['bear_div']}, BullDiv: {entry['bull_div']})")
                
            recalculated_history.append((
                date_str, 
                v3_sig['meta_score'], 
                v3_out['phase'], 
                v3_out['w_bot']
            ))
            updated_entries.append(entry)
        except Exception as e:
            print(f"  Error on {date_str}: {e}")
            updated_entries.append(entry)

    print(f"Saving updated {len(updated_entries)} entries to scores.json...")
    with open(scores_path, 'w', encoding='utf-8') as f:
        json.dump(updated_entries, f, ensure_ascii=False, indent=2)

    print("Success! History recalculated.")

if __name__ == '__main__':
    main()
