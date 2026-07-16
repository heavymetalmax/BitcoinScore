#!/usr/bin/env python3
"""Sync data/history/scores.json → web/score_history.json.

Converts the internal format (date, final_score, phase, w_bot, btc_price)
to the minimal web format (date, score, price) expected by the frontend.

Also copies web/history/scores.json for backwards-compatibility with any
frontend code that reads the full scores history directly.

Usage:
    python3 tools/sync_web_scores.py
"""
import json
import os
import sys

SRC = 'data/history/scores.json'
DST_MINI = 'web/score_history.json'
DST_FULL = 'web/history/scores.json'


def main():
    if not os.path.exists(SRC):
        print(f'sync_web_scores: {SRC} not found — skipping', file=sys.stderr)
        return

    with open(SRC, encoding='utf-8') as f:
        records = json.load(f)

    mini = [
        {'date': r['date'], 'score': r.get('final_score'), 'price': r.get('btc_price')}
        for r in records
        if r.get('date') and r.get('final_score') is not None
    ]

    os.makedirs('web/history', exist_ok=True)

    with open(DST_MINI, 'w', encoding='utf-8') as f:
        json.dump(mini, f, separators=(',', ':'))
    print(f'sync_web_scores: {len(mini)} entries → {DST_MINI}')

    with open(DST_FULL, 'w', encoding='utf-8') as f:
        json.dump(records, f, separators=(',', ':'))
    print(f'sync_web_scores: {len(records)} entries → {DST_FULL}')


if __name__ == '__main__':
    main()
