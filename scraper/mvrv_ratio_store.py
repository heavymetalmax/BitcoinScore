"""Authoritative CoinMetrics MVRV Ratio (Price / Realized Price) Store.

Provides strictly causal daily MVRV Ratio (`CapMVRVCur`) from the open
CoinMetrics Community API (with local JSON cache at
`data/history/mvrv_ratio_history.json`) for Cost-Basis 4-Year Cycle Amplitude
(`CostBasis4Y`) in:
  - scraper/causal_core.py
  - tools/run_causal_backtest.py
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_MVRV_RATIO_PATH = 'data/history/mvrv_ratio_history.json'
COINMETRICS_URL = 'https://community-api.coinmetrics.io/v4/timeseries/asset-metrics'


def _load_existing_series(path: str) -> dict[str, dict[str, Any]]:
    existing: dict[str, dict[str, Any]] = {}
    if not os.path.exists(path):
        return existing
    try:
        raw = json.load(open(path, encoding='utf-8'))
        rows = raw.get('series', raw) if isinstance(raw, dict) else raw
        for r in rows:
            if not isinstance(r, dict) or not r.get('date'):
                continue
            d = str(r['date'])[:10]
            mv = r.get('mvrv_ratio')
            if mv is None:
                continue
            mv_f = float(mv)
            if mv_f <= 0:
                continue
            p_raw = r.get('price_usd')
            existing[d] = {
                'date': d,
                'mvrv_ratio': mv_f,
                'price_usd': float(p_raw) if p_raw is not None else None,
            }
    except Exception:
        pass
    return existing


def _save_series(existing: dict[str, dict[str, Any]], path: str) -> int:
    merged = [existing[d] for d in sorted(existing)]
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(
            {
                'schema': 'coinmetrics_mvrv_ratio_v1',
                'metric': 'CapMVRVCur',
                'count': len(merged),
                'series': merged,
            },
            f,
            separators=(',', ':'),
        )
    return len(merged)


def seed_mvrv_ratio_from_cm_json(cm_json_path: str, path: str = DEFAULT_MVRV_RATIO_PATH) -> int:
    """Seed/merge MVRV Ratio store from a raw CoinMetrics JSON dump."""
    existing = _load_existing_series(path)
    raw = json.load(open(cm_json_path, encoding='utf-8'))
    for row in raw.get('data', []):
        d = str(row.get('time', ''))[:10]
        mv = row.get('CapMVRVCur')
        if not d or mv is None:
            continue
        mv_f = float(mv)
        if mv_f <= 0:
            continue
        p_raw = row.get('PriceUSD')
        existing[d] = {
            'date': d,
            'mvrv_ratio': round(mv_f, 6),
            'price_usd': round(float(p_raw), 4) if p_raw is not None else None,
        }
    return _save_series(existing, path)


def sync_mvrv_ratio_from_coinmetrics(
    path: str = DEFAULT_MVRV_RATIO_PATH,
    timeout: int = 15,
) -> int:
    """Incrementally fetch latest BTC CapMVRVCur from CoinMetrics Community API.

    Fails softly (returns existing cached count) if offline or API unavailable.
    """
    existing = _load_existing_series(path)
    if existing:
        last_d = max(existing)
        start_dt = datetime.date.fromisoformat(last_d) - datetime.timedelta(days=7)
        start_str = start_dt.isoformat()
    else:
        start_str = '2010-07-18'

    params = urllib.parse.urlencode({
        'assets': 'btc',
        'metrics': 'CapMVRVCur,PriceUSD',
        'frequency': '1d',
        'start_time': start_str,
        'page_size': '10000',
    })
    url: str | None = f'{COINMETRICS_URL}?{params}'
    updated = 0
    try:
        while url:
            req = urllib.request.Request(url, headers={'User-Agent': 'BitcoinScore/5.2'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
            for row in payload.get('data', []):
                d = str(row.get('time', ''))[:10]
                mv = row.get('CapMVRVCur')
                if not d or mv is None:
                    continue
                mv_f = float(mv)
                if mv_f <= 0:
                    continue
                p_raw = row.get('PriceUSD')
                existing[d] = {
                    'date': d,
                    'mvrv_ratio': round(mv_f, 6),
                    'price_usd': round(float(p_raw), 4) if p_raw is not None else None,
                }
                updated += 1
            url = payload.get('next_page_url')
    except Exception as exc:
        print(f'Warning: CoinMetrics CapMVRVCur sync failed ({exc}); using cached store ({len(existing)} rows)')
        return len(existing)

    if updated > 0 or not os.path.exists(path):
        return _save_series(existing, path)
    return len(existing)


def load_daily_mvrv_ratio(
    as_of_date: datetime.date | str | None = None,
    path: str = DEFAULT_MVRV_RATIO_PATH,
) -> list[tuple[datetime.date, float]]:
    """Return sorted (date, mvrv_ratio) strictly <= as_of_date."""
    if not os.path.exists(path):
        return []
    as_of_dt: datetime.date | None = None
    if isinstance(as_of_date, str):
        as_of_dt = datetime.date.fromisoformat(as_of_date[:10])
    elif isinstance(as_of_date, datetime.date):
        as_of_dt = as_of_date

    existing = _load_existing_series(path)
    out: list[tuple[datetime.date, float]] = []
    for d_str in sorted(existing):
        d = datetime.date.fromisoformat(d_str)
        if as_of_dt is not None and d > as_of_dt:
            continue
        out.append((d, float(existing[d_str]['mvrv_ratio'])))
    return out
