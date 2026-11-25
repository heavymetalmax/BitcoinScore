#!/usr/bin/env python3
"""Sync data/history/scores.json → web/history/scores.json & web/score_history.json.

Attaches strictly causal 0%-lookahead Smart Purged-IC v5.2 (`causal_btcbri`)
from scraper.causal_core.compute_causal_weekly_history() to every daily record
while preserving `final_score` untouched for invariant checks.

Usage:
    python3 tools/sync_web_scores.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scraper.causal_core import compute_causal_weekly_history

SRC = 'data/history/scores.json'
DST_FULL = 'web/history/scores.json'
DST_COMPACT = 'web/score_history.json'


def main():
    if not os.path.exists(SRC):
        print(f'sync_web_scores: {SRC} not found — skipping', file=sys.stderr)
        return

    with open(SRC, encoding='utf-8') as f:
        records = json.load(f)

    weekly = compute_causal_weekly_history(
        ohlc_path='data/history/btc_weekly_ohlc.json',
        scores_path=SRC,
        mvrv_ratio_path='data/history/mvrv_ratio_history.json',
    )
    # Build sorted (week_close, causal_v52) list so each day uses the latest CLOSED week
    wk_pts = [
        (w['week_close'], w['causal_v52'])
        for w in weekly
        if w.get('week_close') and w.get('causal_v52') is not None
    ]
    wk_pts.sort(key=lambda x: x[0])

    # Also read live causal_btcbri from data/data.json if available for dates after last closed week
    live_cbri = None
    live_date = None
    if os.path.exists('data/data.json'):
        try:
            d_live = json.load(open('data/data.json', encoding='utf-8'))
            live_cbri = d_live.get('causal_btcbri')
            live_date = str(d_live.get('date', ''))[:10]
        except Exception:
            pass

    wk_idx = 0
    last_cbri = None
    out_full = []
    compact = []
    for r in records:
        d = str(r.get('date', ''))[:10]
        while wk_idx < len(wk_pts) and wk_pts[wk_idx][0] < d:
            last_cbri = wk_pts[wk_idx][1]
            wk_idx += 1
        cbri = last_cbri
        if live_cbri is not None and live_date and d >= live_date:
            cbri = live_cbri

        rec = dict(r)
        if cbri is not None:
            rec['causal_btcbri'] = cbri
        out_full.append(rec)

        sc = cbri if cbri is not None else r.get('final_score')
        if d and sc is not None:
            compact.append({
                'date': d,
                'score': sc,
                'causal_btcbri': cbri,
                'legacy_score': r.get('final_score'),
                'price': r.get('btc_price'),
            })

    os.makedirs('web/history', exist_ok=True)
    with open(DST_FULL, 'w', encoding='utf-8') as f:
        json.dump(out_full, f, separators=(',', ':'))
    with open(DST_COMPACT, 'w', encoding='utf-8') as f:
        json.dump(compact, f, separators=(',', ':'))
    print(f'sync_web_scores: {len(out_full)} entries (with causal_btcbri) → {DST_FULL} & {DST_COMPACT}')


if __name__ == '__main__':
    main()
