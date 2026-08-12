#!/usr/bin/env python3
"""Add the first causally knowable confirmation date to cycle extrema."""
import json
from pathlib import Path

EXTREMES_PATH = Path('data/cycle_extremes.json')
SCORES_PATH = Path('data/history/scores.json')


def confirmation_date(extreme: dict, prices: list[tuple[str, float]], move_pct: float):
    threshold = float(extreme['price']) * (1 - move_pct / 100 if extreme['type'] == 'TOP'
                                           else 1 + move_pct / 100)
    for date, price in prices:
        if date <= extreme['date']:
            continue
        if extreme['type'] == 'TOP' and price <= threshold:
            return date
        if extreme['type'] == 'BOTTOM' and price >= threshold:
            return date
    return None


def main():
    payload = json.loads(EXTREMES_PATH.read_text())
    rows = json.loads(SCORES_PATH.read_text())
    prices = sorted((r['date'][:10], float(r['btc_price'])) for r in rows
                    if r.get('date') and r.get('btc_price') is not None)
    move_pct = float(payload['min_move_pct'])
    for extreme in payload['extremes']:
        extreme['confirmed_at'] = confirmation_date(extreme, prices, move_pct)
    payload['confirmation_semantics'] = (
        'First later daily close that moved min_move_pct in the opposite direction; '
        'null means the extreme was not yet causally confirmed.'
    )
    EXTREMES_PATH.write_text(json.dumps(payload, indent=2) + '\n')


if __name__ == '__main__':
    main()
