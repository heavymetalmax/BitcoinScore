#!/usr/bin/env python3
"""One-time merge: build the single canonical scores.json from 2016 to today.

Rules:
  - scores.json (V3, live scraper) is authoritative for every date it covers
  - backfill_scores.json fills in 2016-2017 (dates missing from scores.json)
  - btc_price_history.json + daily_vector.json provide BTC price for all entries
  - Result written back to data/history/scores.json

After this run, export_web.py will pick it up and push to the site.

Usage:
    python3 tools/unify_scores.py
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
    prices = {}
    ph_path = os.path.join(ROOT, 'data', 'history', 'btc_price_history.json')
    if os.path.exists(ph_path):
        ph = load(ph_path)
        series = ph.get('series', ph) if isinstance(ph, dict) else ph
        for row in series:
            d = row.get('date', '')
            v = row.get('close') or row.get('price')
            if d and v:
                prices[d] = round(float(v), 2)

    dv_path = os.path.join(ROOT, 'data', 'history', 'daily_vector.json')
    if os.path.exists(dv_path):
        for row in load(dv_path):
            d = row.get('date', '')
            v = row.get('btc_price') or row.get('price')
            if d and v and d not in prices:
                prices[d] = round(float(v), 2)

    main_path = os.path.join(ROOT, 'data', 'data.json')
    if os.path.exists(main_path):
        m = load(main_path)
        d = (m.get('timestamp') or '')[:10]
        v = m.get('btc_price')
        if d and v:
            prices[d] = round(float(v), 2)

    print(f'unify_scores: price index built — {len(prices)} dates ({min(prices)}→{max(prices)})')
    return prices


def main():
    scores_path = os.path.join(ROOT, 'data', 'history', 'scores.json')
    bf_path     = os.path.join(ROOT, 'data', 'history', 'backfill_scores.json')

    # 1. Load V3 canonical scores (authoritative)
    v3 = {}
    if os.path.exists(scores_path):
        for e in load(scores_path):
            d = e.get('date', '')
            if d:
                v3[d] = e
    print(f'unify_scores: V3 scores loaded — {len(v3)} dates ({min(v3)}→{max(v3)})')

    # 2. Fill 2016-2017 gap from backfill (only dates NOT in V3)
    gap_filled = 0
    if os.path.exists(bf_path):
        raw = load(bf_path)
        series = raw.get('series', raw) if isinstance(raw, dict) else raw
        for row in series:
            d = row.get('date', '')
            if d and d not in v3:
                # Use v2_final as final_score for gap entries (best we have)
                v3[d] = {
                    'date':        d,
                    'final_score': row.get('v2_final') or row.get('v1_final'),
                    'phase':       row.get('regime', 'UNKNOWN').upper(),
                    'w_bot':       None,
                    'btc_price':   None,
                    'source':      'backfill_v2',
                }
                gap_filled += 1
    print(f'unify_scores: gap filled — {gap_filled} entries from backfill (2016-2017)')

    # 3. Attach BTC price to all entries
    prices = build_price_index()
    price_added = 0
    for d, entry in v3.items():
        if not entry.get('btc_price') and d in prices:
            entry['btc_price'] = prices[d]
            price_added += 1
    print(f'unify_scores: btc_price attached to {price_added} entries')

    # 4. Write unified scores.json
    unified = sorted(v3.values(), key=lambda e: e['date'])
    save(scores_path, unified)

    with_price = sum(1 for e in unified if e.get('btc_price') and e['btc_price'] > 0)
    print(f'unify_scores: done — {len(unified)} entries ({unified[0]["date"]}→{unified[-1]["date"]}), {with_price} with price')


if __name__ == '__main__':
    main()
