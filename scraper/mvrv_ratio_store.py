"""Authoritative CoinMetrics MVRV Ratio & Market/Realized Cap Store.

Provides strictly causal daily MVRV Ratio (`CapMVRVCur`) and Market Cap
(`CapMrktCurUSD`, hence Realized Cap = `CapMrktCurUSD / CapMVRVCur`) from the
open CoinMetrics Community API (cached at `data/history/mvrv_ratio_history.json`)
for Cost-Basis 4Y Cycle Amplitude and Market-Cap-Adjusted MVRV in:
  - scraper/causal_core.py
  - tools/run_causal_backtest.py
"""
from __future__ import annotations

import csv
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
            mc_raw = r.get('market_cap_usd')
            rc_raw = r.get('realized_cap_usd')
            mc_f = float(mc_raw) if mc_raw is not None else None
            rc_f = float(rc_raw) if rc_raw is not None else ((mc_f / mv_f) if (mc_f is not None and mc_f > 0) else None)
            existing[d] = {
                'date': d,
                'mvrv_ratio': mv_f,
                'price_usd': float(p_raw) if p_raw is not None else None,
                'market_cap_usd': mc_f,
                'realized_cap_usd': rc_f,
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
                'schema': 'coinmetrics_mvrv_ratio_v2',
                'metrics': ['CapMVRVCur', 'CapMrktCurUSD', 'PriceUSD'],
                'count': len(merged),
                'series': merged,
            },
            f,
            separators=(',', ':'),
        )
    return len(merged)


def seed_mvrv_ratio_from_cm_csv(cm_csv_path: str, path: str = DEFAULT_MVRV_RATIO_PATH) -> int:
    """Seed/merge MVRV Ratio + Market/Realized Cap store from a CoinMetrics CSV dump."""
    existing = _load_existing_series(path)
    with open(cm_csv_path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            d = str(row.get('time', ''))[:10]
            mv = row.get('CapMVRVCur')
            if not d or not mv:
                continue
            mv_f = float(mv)
            if mv_f <= 0:
                continue
            p_raw = row.get('PriceUSD')
            mc_raw = row.get('CapMrktCurUSD')
            mc_f = float(mc_raw) if mc_raw else None
            rc_f = (mc_f / mv_f) if (mc_f is not None and mc_f > 0) else None
            existing[d] = {
                'date': d,
                'mvrv_ratio': round(mv_f, 6),
                'price_usd': round(float(p_raw), 4) if p_raw else None,
                'market_cap_usd': round(mc_f, 2) if mc_f is not None else None,
                'realized_cap_usd': round(rc_f, 2) if rc_f is not None else None,
            }
    return _save_series(existing, path)


def sync_mvrv_ratio_from_coinmetrics(
    path: str = DEFAULT_MVRV_RATIO_PATH,
    timeout: int = 15,
) -> int:
    """Incrementally fetch latest BTC CapMVRVCur + CapMrktCurUSD from CoinMetrics Community API.

    Fails softly (returns existing cached count) if offline or API unavailable.
    """
    existing = _load_existing_series(path)
    has_cap = bool(existing) and all(
        existing[d].get('market_cap_usd') is not None
        for d in sorted(existing)[-30:]
    )
    if existing and has_cap:
        last_d = max(existing)
        start_dt = datetime.date.fromisoformat(last_d) - datetime.timedelta(days=7)
        start_str = start_dt.isoformat()
    else:
        start_str = '2010-07-18'

    params = urllib.parse.urlencode({
        'assets': 'btc',
        'metrics': 'CapMVRVCur,CapMrktCurUSD,PriceUSD',
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
                mc_raw = row.get('CapMrktCurUSD')
                mc_f = float(mc_raw) if mc_raw is not None else None
                rc_f = (mc_f / mv_f) if (mc_f is not None and mc_f > 0) else None
                existing[d] = {
                    'date': d,
                    'mvrv_ratio': round(mv_f, 6),
                    'price_usd': round(float(p_raw), 4) if p_raw is not None else None,
                    'market_cap_usd': round(mc_f, 2) if mc_f is not None else None,
                    'realized_cap_usd': round(rc_f, 2) if rc_f is not None else None,
                }
                updated += 1
            url = payload.get('next_page_url')
    except Exception as exc:
        print(f'Warning: CoinMetrics CapMVRVCur sync failed ({exc}); using cached store ({len(existing)} rows)')
        return len(existing)

    if updated > 0 or not os.path.exists(path):
        return _save_series(existing, path)
    return len(existing)


def load_daily_mvrv_and_cap(
    as_of_date: datetime.date | str | None = None,
    path: str = DEFAULT_MVRV_RATIO_PATH,
) -> list[tuple[datetime.date, float, float | None]]:
    """Return sorted (date, mvrv_ratio, realized_cap_usd) strictly <= as_of_date."""
    if not os.path.exists(path):
        return []
    as_of_dt: datetime.date | None = None
    if isinstance(as_of_date, str):
        as_of_dt = datetime.date.fromisoformat(as_of_date[:10])
    elif isinstance(as_of_date, datetime.date):
        as_of_dt = as_of_date

    existing = _load_existing_series(path)
    out: list[tuple[datetime.date, float, float | None]] = []
    for d_str in sorted(existing):
        d = datetime.date.fromisoformat(d_str)
        if as_of_dt is not None and d > as_of_dt:
            continue
        row = existing[d_str]
        out.append((d, float(row['mvrv_ratio']), row.get('realized_cap_usd')))
    return out


def load_daily_mvrv_ratio(
    as_of_date: datetime.date | str | None = None,
    path: str = DEFAULT_MVRV_RATIO_PATH,
) -> list[tuple[datetime.date, float]]:
    """Return sorted (date, mvrv_ratio) strictly <= as_of_date."""
    return [(d, mv) for d, mv, _ in load_daily_mvrv_and_cap(as_of_date=as_of_date, path=path)]
