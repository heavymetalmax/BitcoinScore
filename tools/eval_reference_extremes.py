#!/usr/bin/env python3
"""Evaluate Causal v5.2 & Legacy scores against data/reference_extremes.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.causal_core import compute_causal_btcbri, compute_causal_d1_state, compute_causal_mvrv_rank


def main() -> None:
    ref = json.loads((ROOT / 'data/reference_extremes.json').read_text(encoding='utf-8'))
    scores = {r['date'][:10]: r for r in json.loads((ROOT / 'data/history/scores.json').read_text(encoding='utf-8')) if r.get('date')}

    print(f"{'Date':10s} {'Kind':6s} {'WeekOpen':10s} {'Label':28s} | {'v3':>4s} {'v5.2':>5s} {'CB4Y':>5s} {'MVRV_R':>6s} {'WT1%':>5s} {'SMA40':>5s} {'CB1W':>4s}")
    print('-' * 105)
    for row in ref['extremes']:
        d = row['date']
        eval_d = row['week_eval_date']
        sc_row = scores.get(d, {})
        d1 = compute_causal_d1_state(
            as_of_date=eval_d,
            ohlc_path=str(ROOT / 'data/history/btc_weekly_ohlc.json'),
            scores_path=str(ROOT / 'data/history/scores.json'),
            mvrv_ratio_path=str(ROOT / 'data/history/mvrv_ratio_history.json'),
        )
        mv_info = compute_causal_mvrv_rank(
            sc_row.get('mvrv'),
            as_of_date=d,
            scores_path=str(ROOT / 'data/history/scores.json'),
            puell_today=sc_row.get('puell'),
            cvdd_today=sc_row.get('cvdd_ratio'),
        )
        bri52 = compute_causal_btcbri(mv_info, d1)
        amp = d1.get('cycle_amplitude') or {}
        v3 = sc_row.get('final_score')
        v3_s = str(v3) if v3 is not None else '—'
        b52_s = f"{bri52:.1f}" if bri52 is not None else '—'
        amp_s = f"{amp['amp_score']:.1f}" if amp.get('amp_score') is not None else '—'
        mvr_s = f"{amp['mvrv_ratio']:.2f}x" if amp.get('mvrv_ratio') is not None else '—'
        wt1_s = f"{d1['wt1_causal_rank']:.1f}" if d1.get('wt1_causal_rank') is not None else '—'
        sma_s = 'IN' if d1.get('sma40_in_pos') else 'OUT'
        cb_s = 'IN' if d1.get('cb_in_pos') else 'OUT'
        print(
            f"{d:10s} {row['kind']:6s} {row['week_open']:10s} {row['label']:28s} | "
            f"{v3_s:>4s} {b52_s:>5s} {amp_s:>5s} {mvr_s:>6s} {wt1_s:>5s} {sma_s:>5s} {cb_s:>4s}"
        )


if __name__ == '__main__':
    main()
