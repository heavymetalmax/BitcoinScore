#!/usr/bin/env python3
"""Attach max_drawdown_365d labels to existing training features for V5B.

Reads data/training_features.json (all V5 features already built).
Adds 'label_b' = max future drawdown over next 365 calendar days.

Label semantics:
  0   = perfect buy (price only goes up for the next year)
  75+ = dangerous to hold (major bear market follows)

Rows where future 365d window is incomplete (last ~year of data) get label_b=None
and are excluded from V5B training.

Output: data/training_features_b.json
"""
import datetime
import json
from pathlib import Path

HORIZON_DAYS = 365
MIN_COVERAGE_DAYS = 300


def max_drawdown_365(date_str: str, prices: dict[str, float]) -> float | None:
    """Compute max drawdown (%) from date_str over the next 365 calendar days.

    Returns None unless the complete 365-calendar-day horizon has elapsed and
    at least MIN_COVERAGE_DAYS contain prices. This prevents recent partial
    windows from being mislabeled as unusually safe.
    """
    try:
        d0 = datetime.date.fromisoformat(date_str[:10])
    except ValueError:
        return None
    p0 = prices.get(date_str[:10])
    if p0 is None or p0 <= 0:
        return None

    horizon_end = d0 + datetime.timedelta(days=HORIZON_DAYS)
    if not prices or datetime.date.fromisoformat(max(prices)) < horizon_end:
        return None

    future = []
    for i in range(1, HORIZON_DAYS + 1):
        d = (d0 + datetime.timedelta(days=i)).isoformat()
        p = prices.get(d)
        if p is not None:
            future.append(p)

    if len(future) < MIN_COVERAGE_DAYS:
        return None

    min_p = min(future)
    return round(max(0.0, (p0 - min_p) / p0 * 100), 2)


def main():
    feats_path = Path('data/training_features.json')
    scores_path = Path('data/history/scores.json')

    print('Loading training features...')
    rows = json.loads(feats_path.read_text())

    print('Loading price series from scores.json...')
    scores = json.loads(scores_path.read_text())
    prices = {}
    for r in scores:
        d = r.get('date', '')[:10]
        p = r.get('btc_price')
        if d and p is not None:
            prices[d] = float(p)
    print(f'  Price points available: {len(prices)}  '
          f'({min(prices)} → {max(prices)})')

    labeled = skipped = 0
    for row in rows:
        dd = max_drawdown_365(row.get('date', ''), prices)
        row['label_b'] = dd
        if dd is not None:
            labeled += 1
        else:
            skipped += 1

    out_path = Path('data/training_features_b.json')
    out_path.write_text(json.dumps(rows, indent=None, separators=(',', ':')))
    print(f'  Labeled:  {labeled}')
    print(f'  Skipped:  {skipped}  (incomplete 365d horizon/coverage — excluded from training)')
    print(f'  Written → {out_path}')

    # Quick validation on key cycle dates
    print('\nKey date check:')
    check = {
        '2018-12-15': 'Dec 2018 bottom',
        '2019-06-26': 'Jun 2019 local top',
        '2021-11-10': 'Nov 2021 ATH',
        '2022-11-21': 'FTX bottom',
        '2024-11-15': 'pre-ATH 2025',
    }
    by_date = {row['date'][:10]: row for row in rows}
    for date, label in check.items():
        row = by_date.get(date)
        if row:
            p = prices.get(date, '?')
            lb = row.get('label_b')
            print(f'  {date}  {label:22s}  ${p:,.0f}  max_dd_365d={lb}%')


if __name__ == '__main__':
    main()
