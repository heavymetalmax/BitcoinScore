"""Authoritative DefiLlama Total Stablecoin Supply Store.

Provides strictly causal daily total USD-pegged stablecoin circulating supply
cached at `data/history/stablecoin_history.json` (from DefiLlama open API) for
the zero-cold-start `st_roc4` (26w Stablecoin Supply ROC) sub-index in
`scraper/causal_core.py`.
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.request

DEFAULT_STABLECOIN_PATH = 'data/history/stablecoin_history.json'
DEFILLAMA_STABLES_URL = 'https://stablecoins.llama.fi/stablecoincharts/all'


def _load_existing(path: str) -> dict[str, float]:
    out: dict[str, float] = {}
    if not os.path.exists(path):
        return out
    try:
        raw = json.load(open(path, encoding='utf-8'))
        rows = raw.get('series', raw) if isinstance(raw, dict) else raw
        for r in rows:
            if isinstance(r, dict) and r.get('date') and r.get('supply_usd') is not None:
                v = float(r['supply_usd'])
                if v > 0:
                    out[str(r['date'])[:10]] = v
    except Exception:
        pass
    return out


def _save_series(existing: dict[str, float], path: str) -> int:
    merged = [{'date': d, 'supply_usd': round(existing[d], 2)} for d in sorted(existing)]
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(
            {
                'schema': 'defillama_stables_v1',
                'source': 'defillama_stablecoincharts_all',
                'count': len(merged),
                'series': merged,
            },
            f,
            separators=(',', ':'),
        )
    return len(merged)


def sync_stablecoins_from_defillama(
    path: str = DEFAULT_STABLECOIN_PATH,
    timeout: int = 20,
) -> int:
    """Fetch daily total USD stablecoin supply from DefiLlama open API.

    Fails softly (returns existing cached count) if offline or API unavailable.
    """
    existing = _load_existing(path)
    updated = 0
    try:
        req = urllib.request.Request(DEFILLAMA_STABLES_URL, headers={'User-Agent': 'Mozilla/5.0'})
        rows = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8'))
        for r in rows:
            t = r.get('date')
            circ = (r.get('totalCirculatingUSD') or {}).get('peggedUSD') or (r.get('totalCirculating') or {}).get('peggedUSD')
            if not t or circ is None:
                continue
            vf = float(circ)
            if vf <= 0:
                continue
            d = datetime.datetime.fromtimestamp(int(t), tz=datetime.timezone.utc).strftime('%Y-%m-%d')
            existing[d] = round(vf, 2)
            updated += 1
    except Exception as exc:
        print(f'Warning: DefiLlama stablecoins sync failed ({exc}); using cached store ({len(existing)} rows)')
        return len(existing)

    if updated > 0 or not os.path.exists(path):
        return _save_series(existing, path)
    return len(existing)


def load_daily_stablecoins(
    as_of_date: datetime.date | str | None = None,
    path: str = DEFAULT_STABLECOIN_PATH,
) -> list[tuple[datetime.date, float]]:
    """Return sorted (date, supply_usd) strictly <= as_of_date."""
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
