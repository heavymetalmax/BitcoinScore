#!/usr/bin/env python3
"""Single export step: data/ → web/

Reads everything from data/ (single source of truth) and writes all web/ files.
Run after scraper; replaces ad-hoc cp/sync calls scattered across the pipeline.

Usage:
    python3 tools/export_web.py
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
WEB  = os.path.join(ROOT, 'web')


def _read(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, separators=(',', ':'))


def _copy(src, dst):
    if os.path.exists(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return True
    return False


def export_main_data():
    copied = _copy(
        os.path.join(DATA, 'data.json'),
        os.path.join(WEB, 'data.json'),
    )
    if not copied:
        print('export_web: data/data.json missing — aborting', file=sys.stderr)
        sys.exit(1)

    _copy(os.path.join(DATA, 'data_exp.json'),      os.path.join(WEB, 'data_exp.json'))
    _copy(os.path.join(DATA, 'bucket_returns.json'), os.path.join(WEB, 'bucket_returns.json'))
    print('export_web: main data files copied')


def export_score_history():
    src = os.path.join(DATA, 'history', 'scores.json')
    if not os.path.exists(src):
        print('export_web: data/history/scores.json missing — skipping score history')
        return

    records = _read(src)

    mini = [
        {'date': r['date'], 'score': r.get('final_score'), 'price': r.get('btc_price')}
        for r in records
        if r.get('date') and r.get('final_score') is not None
    ]
    _write(os.path.join(WEB, 'score_history.json'), mini)

    os.makedirs(os.path.join(WEB, 'history'), exist_ok=True)
    _write(os.path.join(WEB, 'history', 'scores.json'), records)
    print(f'export_web: score_history ({len(mini)} entries) → web/')


# History files the website reads from web/history/
_HISTORY_FILES = [
    'cipherb.json',
    'daily_vector.json',
    'dxy_scraper_history.json',
    'etf_flows.json',
    'fear_greed.json',
    'funding_rate_history.json',
    'm2.json',
    'smc.json',
    'yield_curve.json',
]


def export_history_files():
    copied = 0
    for fname in _HISTORY_FILES:
        src = os.path.join(DATA, 'history', fname)
        dst = os.path.join(WEB, 'history', fname)
        if _copy(src, dst):
            copied += 1
    print(f'export_web: {copied}/{len(_HISTORY_FILES)} history files copied → web/history/')


def export_sparklines():
    try:
        sys.path.insert(0, ROOT)
        from tools.generate_sparklines import main as gen_sparklines
        gen_sparklines()
        print('export_web: sparklines regenerated → web/sparklines.json')
    except Exception as e:
        print(f'export_web: sparklines generation failed — {e}', file=sys.stderr)


def main():
    os.chdir(ROOT)
    export_main_data()
    export_score_history()
    export_history_files()
    export_sparklines()
    print('export_web: done')


if __name__ == '__main__':
    main()
