#!/usr/bin/env python3
"""Recompute all historical scores (pre-April-2025) with ONE methodology.

- Source: data/history/unified_history.json (metric values per date)
- Unit fixes applied:
    nupl  : stored as fraction (0.48) → compute_score expects percentage (48)
    asopr : stored as (aSOPR−1) e.g. 0.005 → compute_score expects actual 1.005
- Causal normalization: normalizer only sees data up to target_date (no look-ahead)
- Causal TiZ: scores_history built up incrementally as each date is computed
- Live entries (2025-04-01 +) are kept as-is from scores.json
- Result written to data/history/scores.json (ONE database)

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
                        help='start date YYYY-MM-DD (default: earliest in unified_history)')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    from scraper.score import compute_score

    uh_path = os.path.join(ROOT, 'data', 'history', 'unified_history.json')
    sc_path = os.path.join(ROOT, 'data', 'history', 'scores.json')

    uh_raw = load(uh_path)
    uh_series = uh_raw.get('series', uh_raw) if isinstance(uh_raw, dict) else uh_raw
    uh = {e['date']: e for e in uh_series if e.get('date')}
    print(f'recompute: unified_history loaded — {len(uh)} dates ({min(uh)}..{max(uh)})')

    # Load existing scores; keep live entries (Apr 2025+) unchanged
    existing = load(sc_path) if os.path.exists(sc_path) else []
    live_entries = {e['date']: e for e in existing if e.get('date', '') >= LIVE_CUTOFF}
    print(f'recompute: live entries (>= {LIVE_CUTOFF}): {len(live_entries)}')

    # Price lookup
    prices = build_price_index()
    print(f'recompute: price index — {len(prices)} dates')

    # Dates to recompute: unified_history dates before the cutoff
    from_date = args.from_date or min(uh.keys())
    dates_to_process = sorted(
        d for d in uh if from_date <= d < LIVE_CUTOFF
    )
    print(f'recompute: will process {len(dates_to_process)} dates ({dates_to_process[0]}..{dates_to_process[-1]})')

    # Causal scores_history — grows as we compute each date
    # Seed it from any pre-existing entries before from_date (so TiZ isn't blind)
    causal_history = [
        (e['date'], e.get('final_score'), e.get('phase'), e.get('w_bot'))
        for e in existing
        if e.get('date', '') < from_date and e.get('final_score') is not None
    ]
    causal_history.sort(key=lambda t: t[0])
    print(f'recompute: seed causal history: {len(causal_history)} entries before {from_date}')

    results = {}
    ok = 0
    skipped = 0
    errors = 0

    for i, d in enumerate(dates_to_process):
        row = uh[d]

        # Extract raw metrics with unit corrections
        nupl_frac = row.get('nupl')
        asopr_m1 = row.get('asopr')

        raw = {}
        if nupl_frac is not None:
            raw['nupl'] = nupl_frac * 100.0          # fraction → percentage
        v = row.get('mvrv')
        if v is not None:
            raw['mvrv_z_score'] = v
        v = row.get('puell')
        if v is not None:
            raw['puell_multiple'] = v
        v = row.get('rhodl_ratio')
        if v is not None:
            raw['rhodl_ratio'] = v
        if asopr_m1 is not None:
            raw['asopr'] = asopr_m1 + 1.0            # (aSOPR−1) → actual aSOPR
        v = row.get('cvdd_ratio')
        if v is not None:
            raw['cvdd_ratio'] = v
        v = row.get('mayer_multiple')
        if v is not None:
            raw['mayer_multiple'] = v
        v = row.get('fear_greed')
        if v is not None:
            raw['fear_greed'] = v
        v = row.get('cipherb_daily')
        if v is not None:
            raw['cipherb'] = v
        v = row.get('pi_gap_pct')
        if v is not None:
            raw['pi_gap'] = v
        v = row.get('dxy')
        if v is not None:
            raw['dxy'] = v

        # Skip if no on-chain metrics at all (too early / no data)
        onchain_keys = {'nupl', 'mvrv_z_score', 'puell_multiple', 'rhodl_ratio', 'asopr'}
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
            entry = {
                'date':        d,
                'final_score': r['final_score'],
                'phase':       r['phase'],
                'w_bot':       round(r.get('w_bot', 0), 4),
                'btc_price':   prices.get(d),
                'source':      'recompute_v3',
            }
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

    # Merge: recomputed + live
    merged = {}
    for d, entry in results.items():
        merged[d] = entry
    for d, entry in live_entries.items():
        merged[d] = entry

    # Also preserve any dates in existing that are NOT in unified_history and NOT live
    # (e.g. backfill_v2 entries for 2016-2017 gap)
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
