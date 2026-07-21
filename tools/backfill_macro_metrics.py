#!/usr/bin/env python3
"""Backfill macro metrics in data/history/scores.json from FRED.

Fills:
  yield_curve   — FRED T10Y2Y daily (1976→today), forward-fill on weekends/holidays
  m2_yoy        — FRED WM2NS weekly, YoY computed (52-week lag), forward-fill to daily
  lth_supply_pct — fixes unit bug: stored raw BTC count → convert to %
"""
import bisect
import csv
import io
import json
import ssl
import urllib.request
from datetime import date, timedelta
from pathlib import Path


def _fetch_fred(series_id: str) -> list[tuple[str, float]]:
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.88'})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        text = r.read().decode()
    rows = list(csv.reader(io.StringIO(text)))
    return [(r[0], float(r[1])) for r in rows[1:] if r[1] not in ('.', '')]


def build_yield_curve_map() -> dict[str, float]:
    """T10Y2Y daily → {date_str: spread}. Forward-fills to cover weekends."""
    print('Fetching T10Y2Y from FRED...')
    series = _fetch_fred('T10Y2Y')
    print(f'  {len(series)} rows, {series[0][0]} → {series[-1][0]}')
    return {d: v for d, v in series}


def build_m2_yoy_map() -> dict[str, float]:
    """WM2NS weekly → YoY %, then forward-fill per-day."""
    print('Fetching WM2NS from FRED...')
    series = _fetch_fred('WM2NS')
    print(f'  {len(series)} rows, {series[0][0]} → {series[-1][0]}')

    vals_by_date = {d: v for d, v in series}
    sorted_dates = sorted(vals_by_date)

    # Compute YoY for each weekly date
    weekly_yoy: dict[str, float] = {}
    for d in sorted_dates:
        target_past = (date.fromisoformat(d) - timedelta(weeks=52)).isoformat()
        idx = bisect.bisect_right(sorted_dates, target_past) - 1
        if idx < 0:
            continue
        past_val = vals_by_date[sorted_dates[idx]]
        curr_val = vals_by_date[d]
        if past_val:
            weekly_yoy[d] = round((curr_val - past_val) / past_val * 100, 2)

    print(f'  YoY computable for {len(weekly_yoy)} weekly dates')
    return weekly_yoy


def forward_fill(lookup: dict[str, float], query_date: str) -> float | None:
    """Return last known value on or before query_date."""
    keys = sorted(lookup)
    idx = bisect.bisect_right(keys, query_date) - 1
    if idx < 0:
        return None
    return lookup[keys[idx]]


def fix_lth_units(raw_value: float | None) -> float | None:
    """Convert raw BTC count to % if needed. BMP used to return absolute supply."""
    if raw_value is None:
        return None
    if raw_value > 100:
        return round(raw_value / 21_000_000 * 100, 4)
    return raw_value


def main():
    scores_path = Path('data/history/scores.json')
    with open(scores_path) as f:
        history = json.load(f)

    yc_map  = build_yield_curve_map()
    m2_map  = build_m2_yoy_map()

    yc_filled = m2_filled = lth_fixed = 0

    for entry in history:
        d = entry.get('date')
        if not d:
            continue

        # yield_curve
        if entry.get('yield_curve') is None:
            val = forward_fill(yc_map, d)
            if val is not None:
                entry['yield_curve'] = val
                yc_filled += 1

        # m2_yoy — only fill if None; existing MacroMicro values are global M2 (preferred)
        if entry.get('m2_yoy') is None:
            val = forward_fill(m2_map, d)
            if val is not None:
                entry['m2_yoy'] = val
                m2_filled += 1

        # lth_supply_pct — fix unit bug for raw BTC-count entries
        lth = entry.get('lth_supply_pct')
        if lth is not None and lth > 100:
            entry['lth_supply_pct'] = fix_lth_units(lth)
            lth_fixed += 1

    history.sort(key=lambda e: e.get('date', ''))

    with open(scores_path, 'w') as f:
        json.dump(history, f, separators=(',', ':'))

    print(f'\nscores.json updated ({len(history)} total entries):')
    print(f'  yield_curve  filled: {yc_filled}')
    print(f'  m2_yoy       filled: {m2_filled}')
    print(f'  lth units    fixed:  {lth_fixed}')

    # Verify
    yc_count  = sum(1 for e in history if e.get('yield_curve') is not None)
    m2_count  = sum(1 for e in history if e.get('m2_yoy')     is not None)
    lth_count = sum(1 for e in history if e.get('lth_supply_pct') is not None)
    print(f'\nPost-fill coverage:')
    print(f'  yield_curve:    {yc_count}/{len(history)}')
    print(f'  m2_yoy:         {m2_count}/{len(history)}')
    print(f'  lth_supply_pct: {lth_count}/{len(history)}')


if __name__ == '__main__':
    main()
