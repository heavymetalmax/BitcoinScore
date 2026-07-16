#!/usr/bin/env python3
"""Backfill btc_price into data/history/scores.json from btc_price_history.json.

Also fills recent gaps (after btc_price_history.json ends) from daily_vector.json
and the current data.json.

Usage:
    python3 tools/backfill_prices.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, separators=(',', ':'))


def build_price_index():
    """Build date → price dict from all available price sources."""
    prices = {}

    # 1. btc_price_history.json (2017–present, most complete)
    ph_path = os.path.join(ROOT, 'data', 'history', 'btc_price_history.json')
    if os.path.exists(ph_path):
        ph = load(ph_path)
        series = ph.get('series', ph) if isinstance(ph, dict) else ph
        for row in series:
            d = row.get('date', '')
            v = row.get('close') or row.get('price')
            if d and v:
                prices[d] = float(v)
        print(f'backfill_prices: loaded {len(prices)} prices from btc_price_history.json')

    # 2. daily_vector.json (recent ~60 days, fills gap after price history ends)
    dv_path = os.path.join(ROOT, 'data', 'history', 'daily_vector.json')
    if os.path.exists(dv_path):
        dv = load(dv_path)
        added = 0
        for row in (dv if isinstance(dv, list) else []):
            d = row.get('date', '')
            v = row.get('btc_price') or row.get('price')
            if d and v and d not in prices:
                prices[d] = float(v)
                added += 1
        print(f'backfill_prices: added {added} prices from daily_vector.json')

    # 3. data/data.json (today)
    main_path = os.path.join(ROOT, 'data', 'data.json')
    if os.path.exists(main_path):
        main = load(main_path)
        d = (main.get('timestamp') or '')[:10]
        v = main.get('btc_price')
        if d and v and d not in prices:
            prices[d] = float(v)
            print(f'backfill_prices: added today ({d}) = ${v:,.0f} from data.json')

    return prices


def main():
    scores_path = os.path.join(ROOT, 'data', 'history', 'scores.json')
    if not os.path.exists(scores_path):
        print('backfill_prices: scores.json not found — nothing to do')
        return

    scores = load(scores_path)
    prices = build_price_index()

    updated = 0
    for entry in scores:
        d = entry.get('date', '')
        if not entry.get('btc_price') and d in prices:
            entry['btc_price'] = round(prices[d], 2)
            updated += 1

    save(scores_path, scores)

    with_price = sum(1 for e in scores if e.get('btc_price') and e['btc_price'] > 0)
    print(f'backfill_prices: updated {updated} entries — {with_price}/{len(scores)} now have price')


if __name__ == '__main__':
    main()
