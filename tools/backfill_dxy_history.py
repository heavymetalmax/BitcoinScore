#!/usr/bin/env python3
"""Fetch full FRED DTWEXBGS history and save to data/history/dxy_history.json.

DTWEXBGS = Trade Weighted U.S. Dollar Index: Broad, Goods (Jan 2006 = 100).
Data is monthly (published on last business day of each month).

Usage:
    python tools/backfill_dxy_history.py
"""
import sys, os, csv, io, json, ssl, urllib.request

sys.path.insert(0, '.')

_FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS'
_OUT_PATH = 'data/history/dxy_history.json'


def fetch_dxy_series():
    ctx = ssl.create_default_context()
    req = urllib.request.Request(_FRED_URL, headers={'User-Agent': 'curl/7.88'})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        text = r.read().decode()
    rows = list(csv.reader(io.StringIO(text)))
    series = []
    for row in rows[1:]:
        if len(row) < 2 or row[1] in ('.', ''):
            continue
        series.append({'date': row[0], 'value': round(float(row[1]), 4)})
    return series


def main():
    os.makedirs('data/history', exist_ok=True)
    print('Fetching FRED DTWEXBGS history…')
    series = fetch_dxy_series()
    print(f'  Got {len(series)} data points ({series[0]["date"]} → {series[-1]["date"]})')
    payload = {'series': series}
    with open(_OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, separators=(',', ':'))
    print(f'  Saved → {_OUT_PATH}')
    if len(series) < 200:
        print(f'  WARNING: only {len(series)} points — expected 240+')


if __name__ == '__main__':
    main()
