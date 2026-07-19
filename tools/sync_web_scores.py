#!/usr/bin/env python3
"""Sync data/history/scores.json → web/history/scores.json.

The frontend chart reads directly from web/history/scores.json and maps
final_score→score / btc_price→price client-side.

Usage:
    python3 tools/sync_web_scores.py
"""
import json
import os
import sys

SRC = 'data/history/scores.json'
DST_FULL = 'web/history/scores.json'


def main():
    if not os.path.exists(SRC):
        print(f'sync_web_scores: {SRC} not found — skipping', file=sys.stderr)
        return

    with open(SRC, encoding='utf-8') as f:
        records = json.load(f)

    os.makedirs('web/history', exist_ok=True)

    with open(DST_FULL, 'w', encoding='utf-8') as f:
        json.dump(records, f, separators=(',', ':'))
    print(f'sync_web_scores: {len(records)} entries → {DST_FULL}')


if __name__ == '__main__':
    main()
