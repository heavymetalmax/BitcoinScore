#!/usr/bin/env python3
"""ONE-TIME MIGRATION: merge unified_history.json + scores.json → single scores.json.

After this runs, scores.json is the only database:
  date, btc_price, nupl, mvrv, asopr, puell, rhodl_ratio, cvdd_ratio,
  mayer_multiple, fear_greed, cipherb_daily, dxy, funding_rate, m2_yoy,
  pi_gap_pct, final_score, phase, w_bot, source

Unit fixes applied:
  nupl:  fraction (0.704) → percentage (70.4)
  asopr: delta   (0.002)  → actual aSOPR (1.002)

Run ONCE:
    python3 tools/migrate_one_db.py [--dry-run]
"""
import argparse
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

METRIC_FIELDS = [
    'nupl', 'mvrv', 'asopr', 'puell', 'rhodl_ratio', 'cvdd_ratio',
    'mayer_multiple', 'fear_greed', 'cipherb_daily', 'dxy',
    'funding_rate', 'm2_yoy', 'pi_gap_pct',
]


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, separators=(',', ':'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    uh_path = os.path.join(ROOT, 'data', 'history', 'unified_history.json')
    sc_path = os.path.join(ROOT, 'data', 'history', 'scores.json')

    # Load unified_history (source of raw metrics)
    uh_raw = load(uh_path)
    uh_series = uh_raw.get('series', uh_raw) if isinstance(uh_raw, dict) else uh_raw
    uh = {row['date']: row for row in uh_series if row.get('date')}
    print(f'unified_history: {len(uh)} dates ({min(uh)}..{max(uh)})')

    # Load current scores
    sc_list = load(sc_path) if os.path.exists(sc_path) else []
    sc = {row['date']: row for row in sc_list if row.get('date')}
    print(f'scores.json: {len(sc)} dates ({min(sc)}..{max(sc)})')

    # Build unified rows: ALL dates from unified_history + any extra from scores
    all_dates = sorted(set(uh.keys()) | set(sc.keys()))
    print(f'Unified: {len(all_dates)} dates ({all_dates[0]}..{all_dates[-1]})')

    merged = []
    metrics_added = 0

    for date in all_dates:
        uh_row = uh.get(date, {})
        sc_row = sc.get(date, {})

        row = {'date': date}

        # BTC price: prefer scores.json (more recent), fallback to unified_history
        price = sc_row.get('btc_price') or uh_row.get('btc_price')
        if price:
            row['btc_price'] = round(float(price), 2)

        # Raw metrics from unified_history with unit fixes
        added_any = False
        for field in METRIC_FIELDS:
            val = uh_row.get(field)
            if val is None:
                # Try live scores row for metrics (if scraper wrote them)
                val = sc_row.get(field)
            if val is not None:
                val = float(val)
                # Unit fixes
                if field == 'nupl':
                    val = round(val * 100.0, 6)   # fraction → percentage
                elif field == 'asopr':
                    val = round(val + 1.0, 6)      # delta → actual aSOPR
                row[field] = val
                added_any = True
        if added_any:
            metrics_added += 1

        # Score fields from scores.json
        for key in ('final_score', 'phase', 'w_bot', 'source'):
            val = sc_row.get(key)
            if val is not None:
                row[key] = val

        merged.append(row)

    with_score = sum(1 for r in merged if r.get('final_score') is not None)
    with_price = sum(1 for r in merged if r.get('btc_price') is not None)
    with_metrics = sum(1 for r in merged if r.get('nupl') is not None)
    print(f'\nResult: {len(merged)} rows')
    print(f'  with final_score: {with_score}')
    print(f'  with btc_price:   {with_price}')
    print(f'  with nupl:        {with_metrics}')

    # Sample rows
    sample_dates = ['2021-04-14', '2021-11-10', '2025-01-01']
    print('\nSample rows:')
    by_date = {r['date']: r for r in merged}
    for d in sample_dates:
        r = by_date.get(d)
        if r:
            print(f'  {d}: score={r.get("final_score")} nupl={r.get("nupl")} '
                  f'mvrv={r.get("mvrv")} asopr={r.get("asopr")} price={r.get("btc_price")}')

    if args.dry_run:
        print('\n--dry-run: not writing')
        return

    # Backup
    backup = sc_path + '.pre-migration.bak'
    if os.path.exists(sc_path):
        shutil.copy2(sc_path, backup)
        print(f'\nBackup: {backup}')

    save(sc_path, merged)
    print(f'Written: {sc_path}')
    print('\nNEXT STEPS:')
    print('  1. Run: python3 tools/recompute_backtest.py  (rebuild all scores from new DB)')
    print('  2. unified_history.json can be archived after validation')


if __name__ == '__main__':
    main()
