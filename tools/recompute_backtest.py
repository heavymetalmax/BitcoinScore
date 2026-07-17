#!/usr/bin/env python3
"""Recompute all historical scores (pre-April-2025) with ONE methodology.

- Source: data/history/scores.json (the ONE database — metrics + scores per date)
- All units already normalised after migrate_one_db.py:
    nupl in %, asopr as actual value
- Causal normalization: normalizer only sees data up to target_date (no look-ahead)
- Causal TiZ: scores_history built up incrementally as each date is computed
- Live entries (2025-04-01+) kept as-is; their raw metrics preserved
- Result written back to data/history/scores.json

Usage:
    python3 tools/recompute_backtest.py [--dry-run] [--from YYYY-MM-DD]
"""
import argparse
import json
import os
import sys
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LIVE_CUTOFF = '2025-04-01'  # entries from this date onward kept from live scraper


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, separators=(',', ':'))


def build_price_index():
    prices = {}
    ph = os.path.join(ROOT, 'data', 'history', 'btc_price_history.json')
    if os.path.exists(ph):
        raw = load(ph)
        series = raw.get('series', raw) if isinstance(raw, dict) else raw
        for r in series:
            d = r.get('date', '')
            v = r.get('close') or r.get('price')
            if d and v:
                prices[d] = round(float(v), 2)
    dv = os.path.join(ROOT, 'data', 'history', 'daily_vector.json')
    if os.path.exists(dv):
        for r in load(dv):
            d = r.get('date', '')
            v = r.get('btc_price') or r.get('price')
            if d and v and d not in prices:
                prices[d] = round(float(v), 2)
    main = os.path.join(ROOT, 'data', 'data.json')
    if os.path.exists(main):
        m = load(main)
        d = (m.get('timestamp') or '')[:10]
        v = m.get('btc_price')
        if d and v:
            prices[d] = round(float(v), 2)
    return prices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='compute but do not write scores.json')
    parser.add_argument('--from', dest='from_date', default=None,
                        help='start date YYYY-MM-DD (default: earliest in scores.json)')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    from scraper.score import compute_score

    sc_path = os.path.join(ROOT, 'data', 'history', 'scores.json')

    # scores.json is the ONE database: raw metrics + scores per date
    existing = load(sc_path) if os.path.exists(sc_path) else []
    db = {e['date']: e for e in existing if e.get('date')}
    print(f'recompute: scores.json loaded — {len(db)} dates ({min(db)}..{max(db)})')

    live_entries = {d: e for d, e in db.items() if d >= LIVE_CUTOFF}
    print(f'recompute: live entries (>= {LIVE_CUTOFF}): {len(live_entries)}')

    # Price lookup (scores.json has btc_price; this fills any gaps)
    prices = build_price_index()
    print(f'recompute: price index — {len(prices)} dates')

    # Dates to recompute: all historical rows that have at least one on-chain metric
    from_date = args.from_date or min(db.keys())
    dates_to_process = sorted(
        d for d, e in db.items()
        if from_date <= d < LIVE_CUTOFF and any(e.get(k) is not None
            for k in ('nupl', 'mvrv', 'puell', 'rhodl_ratio', 'asopr'))
    )
    print(f'recompute: will process {len(dates_to_process)} dates ({dates_to_process[0]}..{dates_to_process[-1]})')

    # Causal scores_history — grows as we compute each date
    causal_history = [
        (e['date'], e.get('final_score'), e.get('phase'), e.get('w_bot'))
        for d, e in db.items()
        if d < from_date and e.get('final_score') is not None
    ]
    causal_history.sort(key=lambda t: t[0])
    print(f'recompute: seed causal history: {len(causal_history)} entries before {from_date}')

    results = {}
    ok = 0
    skipped = 0
    errors = 0

    for i, d in enumerate(dates_to_process):
        row = db[d]

        # Raw metrics — units already correct in scores.json after migration:
        #   nupl in %, asopr as actual value
        raw = {}
        v = row.get('nupl')
        if v is not None:
            raw['nupl'] = float(v)
        v = row.get('mvrv')
        if v is not None:
            raw['mvrv'] = float(v)
        v = row.get('puell')
        if v is not None:
            raw['puell_multiple'] = float(v)
        v = row.get('rhodl_ratio')
        if v is not None:
            raw['rhodl_ratio'] = float(v)
        v = row.get('asopr')
        if v is not None:
            raw['asopr'] = float(v)
        v = row.get('cvdd_ratio')
        if v is not None:
            raw['cvdd_ratio'] = float(v)
        v = row.get('mayer_multiple')
        if v is not None:
            raw['mayer_multiple'] = float(v)
        v = row.get('fear_greed')
        if v is not None:
            raw['fear_greed'] = float(v)
        v = row.get('cipherb_daily')
        if v is not None:
            raw['cipherb'] = float(v)
        v = row.get('pi_gap_pct')
        if v is not None:
            raw['pi_gap'] = float(v)
        v = row.get('dxy')
        if v is not None:
            raw['dxy'] = float(v)
        v = row.get('m2_yoy') or row.get('m2_mom')
        if v is not None:
            raw['m2_mom'] = float(v)
        v = row.get('yield_curve_spread') or row.get('yield_curve')
        if v is not None:
            raw['yield_curve'] = float(v)
        v = row.get('etf_flows')
        if v is not None:
            raw['etf_flows'] = float(v)

        # Skip if no on-chain metrics at all (too early / no data)
        onchain_keys = {'nupl', 'mvrv', 'puell_multiple', 'rhodl_ratio', 'asopr'}
        if not any(k in raw for k in onchain_keys):
            skipped += 1
            if args.verbose:
                print(f'  SKIP {d} — no on-chain metrics')
            continue

        try:
            r = compute_score(
                raw_metrics=raw,
                target_date=d,
                scores_history=list(causal_history),   # pass causal copy
                btc_price=prices.get(d),
            )
            # Preserve all existing fields (raw metrics, btc_price) + update score fields
            entry = dict(db[d])
            entry['final_score'] = r['final_score']
            entry['phase']       = r['phase']
            entry['w_bot']       = round(r.get('w_bot', 0), 4)
            entry['source']      = 'recompute_v3'
            if prices.get(d) and not entry.get('btc_price'):
                entry['btc_price'] = prices[d]
            results[d] = entry
            # Append to causal history for subsequent dates
            causal_history.append((d, r['final_score'], r['phase'], r.get('w_bot')))
            ok += 1

            if args.verbose or (i % 200 == 0):
                print(f'  [{i+1}/{len(dates_to_process)}] {d}: score={r["final_score"]} phase={r["phase"]}')
        except Exception as e:
            errors += 1
            if args.verbose:
                print(f'  ERROR {d}: {e}')

    print(f'\nrecompute: done — {ok} computed, {skipped} skipped, {errors} errors')

    if args.dry_run:
        print('recompute: --dry-run — NOT writing scores.json')
        # Print sample comparison
        sample_dates = ['2021-12-30', '2022-06-15', '2023-01-01', '2024-01-15']
        old_map = {e['date']: e.get('final_score') for e in existing}
        print('\nSample comparison (old → new):')
        for sd in sample_dates:
            old = old_map.get(sd, '?')
            new = results.get(sd, {}).get('final_score', '?')
            print(f'  {sd}: {old} → {new}')
        return

    # Merge: recomputed + live + any rows without metrics (metric-only or score-only rows)
    merged = {}
    for d, entry in results.items():
        merged[d] = entry
    for d, entry in live_entries.items():
        merged[d] = entry
    for e in existing:
        d = e.get('date', '')
        if d and d not in merged:
            merged[d] = e

    sorted_entries = sorted(merged.values(), key=lambda e: e['date'])
    save(sc_path, sorted_entries)

    with_price = sum(1 for e in sorted_entries if e.get('btc_price'))
    print(f'recompute: wrote {len(sorted_entries)} entries to scores.json ({with_price} with price)')
    print(f'  range: {sorted_entries[0]["date"]}..{sorted_entries[-1]["date"]}')


if __name__ == '__main__':
    main()
