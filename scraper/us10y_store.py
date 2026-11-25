"""Authoritative US 10-Year Treasury Yield (^TNX / US10Y) Store.

Provides strictly causal daily/weekly US10Y yield history cached at
`data/history/us10y_history.json` (from Yahoo Finance `^TNX`) for
Smart Purged-IC 13w bond-yield pressure in `scraper/causal_core.py`.
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.request
from typing import Any

DEFAULT_US10Y_PATH = 'data/history/us10y_history.json'
YAHOO_TNX_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?period1=1293840000&period2=1900000000&interval=1d'


def _load_existing(path: str) -> dict[str, float]:
    out: dict[str, float] = {}
    if not os.path.exists(path):
        return out
    try:
        raw = json.load(open(path, encoding='utf-8'))
        rows = raw.get('series', raw) if isinstance(raw, dict) else raw
        for r in rows:
            if isinstance(r, dict) and r.get('date') and r.get('yield_pct') is not None:
                v = float(r['yield_pct'])
                if v > 0:
                    out[str(r['date'])[:10]] = v
    except Exception:
        pass
    return out


def _save_series(existing: dict[str, float], path: str) -> int:
    merged = [{'date': d, 'yield_pct': round(existing[d], 4)} for d in sorted(existing)]
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(
            {
                'schema': 'us10y_tnx_v1',
                'source': 'yahoo_^TNX',
                'count': len(merged),
                'series': merged,
            },
            f,
            separators=(',', ':'),
        )
    return len(merged)


def sync_us10y_from_yahoo(
    path: str = DEFAULT_US10Y_PATH,
    timeout: int = 15,
) -> int:
    """Incrementally fetch US 10Y Treasury yield (^TNX) from Yahoo Finance.

    Fails softly (returns existing cached count) if offline or API unavailable.
    """
    existing = _load_existing(path)
    if existing:
        last_d = datetime.date.fromisoformat(max(existing))
        p1 = int(datetime.datetime(last_d.year, last_d.month, last_d.day, tzinfo=datetime.timezone.utc).timestamp()) - 14 * 86400
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?period1={p1}&period2=1900000000&interval=1d'
    else:
        url = YAHOO_TNX_URL

    updated = 0
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        payload = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8'))
        res = payload['chart']['result'][0]
        ts = res.get('timestamp') or []
        closes = (res.get('indicators', {}).get('quote') or [{}])[0].get('close') or []
        for t, v in zip(ts, closes):
            if t is None or v is None:
                continue
            vf = float(v)
            if vf <= 0 or vf > 25:
                continue
            d = datetime.datetime.fromtimestamp(int(t), tz=datetime.timezone.utc).strftime('%Y-%m-%d')
            existing[d] = round(vf, 4)
            updated += 1
    except Exception as exc:
        print(f'Warning: US10Y (^TNX) sync failed ({exc}); using cached store ({len(existing)} rows)')
        return len(existing)

    if updated > 0 or not os.path.exists(path):
        return _save_series(existing, path)
    return len(existing)


def load_daily_us10y(
    as_of_date: datetime.date | str | None = None,
    path: str = DEFAULT_US10Y_PATH,
) -> list[tuple[datetime.date, float]]:
    """Return sorted (date, yield_pct) strictly <= as_of_date."""
    if not os.path.exists(path):
        return []
    as_of_dt: datetime.date | None = None
    if isinstance(as_of_date, str):
        as_of_dt = datetime.date.fromisoformat(as_of_date[:10])
    elif isinstance(as_of_date, datetime.date):
        as_of_dt = as_of_date

    existing = _load_existing(path)
    out: list[tuple[datetime.date, float]] = []
    for d_str in sorted(existing):
        d = datetime.date.fromisoformat(d_str)
        if as_of_dt is not None and d > as_of_dt:
            continue
        out.append((d, existing[d_str]))
    return out
