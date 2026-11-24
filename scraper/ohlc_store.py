"""Authoritative Weekly Closed-Bar OHLC Store (Monday-opening, real H/L/O/C).

Provides a single consistent source of strictly closed weekly BTC/USD OHLC bars
for:
  - scraper/tail_risk.py  (13-week drawdown tail-risk classifier B1_vol13)
  - scraper/causal_core.py (SMA40w structural trend + causal D1 state replay)
  - tools/run_causal_backtest.py (zero-lookahead production-parity backtest)
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any

DEFAULT_OHLC_PATH = 'data/history/btc_weekly_ohlc.json'


def load_closed_weekly_ohlc(
    as_of_date: datetime.date | str | None = None,
    ohlc_path: str = DEFAULT_OHLC_PATH,
) -> list[dict[str, Any]]:
    """Load strictly CLOSED Monday-opening weekly OHLC bars (week_close < as_of_date)."""
    if not os.path.exists(ohlc_path):
        return []
    if as_of_date is None:
        as_of = datetime.date.today()
    elif isinstance(as_of_date, str):
        as_of = datetime.date.fromisoformat(as_of_date[:10])
    else:
        as_of = as_of_date

    try:
        raw = json.load(open(ohlc_path, encoding='utf-8'))
        rows = raw.get('series', raw) if isinstance(raw, dict) else raw
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get('date'):
            continue
        wk_open = datetime.date.fromisoformat(str(r['date'])[:10])
        wk_close = wk_open + datetime.timedelta(days=6)
        if wk_close >= as_of:
            continue
        op, hi, lo, cl = r.get('open'), r.get('high'), r.get('low'), r.get('close')
        if None in (op, hi, lo, cl):
            continue
        op_f, hi_f, lo_f, cl_f = float(op), float(hi), float(lo), float(cl)
        if op_f <= 0 or hi_f <= 0 or lo_f <= 0 or cl_f <= 0:
            continue
        out.append({
            'week_open': wk_open,
            'week_close': wk_close,
            'date': wk_open.isoformat(),
            'open': op_f,
            'high': hi_f,
            'low': lo_f,
            'close': cl_f,
            'source': r.get('source', 'weekly_ohlc_archive'),
        })
    out.sort(key=lambda x: x['week_open'])
    return out


def sync_weekly_ohlc_from_daily(
    daily_bars: list[dict[str, Any]],
    as_of_date: datetime.date | str | None = None,
    ohlc_path: str = DEFAULT_OHLC_PATH,
) -> int:
    """Aggregate real daily OHLC bars (e.g. Kraken 1D) into CLOSED Monday-opening weekly bars.

    Only writes fully closed 7-day weeks with week_close < as_of_date.
    Preserves historical bars already stored in ohlc_path.
    Returns total number of weekly bars stored.
    """
    if not daily_bars:
        return 0
    if as_of_date is None:
        as_of = datetime.date.today()
    elif isinstance(as_of_date, str):
        as_of = datetime.date.fromisoformat(as_of_date[:10])
    else:
        as_of = as_of_date

    existing: dict[str, dict[str, Any]] = {}
    if os.path.exists(ohlc_path):
        try:
            raw = json.load(open(ohlc_path, encoding='utf-8'))
            for r in (raw.get('series', raw) if isinstance(raw, dict) else raw):
                if isinstance(r, dict) and r.get('date'):
                    existing[str(r['date'])[:10]] = r
        except Exception:
            pass

    buckets: dict[datetime.date, list[tuple[datetime.date, float, float, float, float]]] = {}
    for b in daily_bars:
        ts = b.get('timestamp')
        d_str = b.get('date')
        if ts is not None:
            d = datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc).date()
        elif d_str:
            d = datetime.date.fromisoformat(str(d_str)[:10])
        else:
            continue
        op, hi, lo, cl = b.get('open'), b.get('high'), b.get('low'), b.get('close')
        if None in (op, hi, lo, cl):
            continue
        wk_open = d - datetime.timedelta(days=d.weekday())
        buckets.setdefault(wk_open, []).append((d, float(op), float(hi), float(lo), float(cl)))

    for wk_open, pts in buckets.items():
        wk_close = wk_open + datetime.timedelta(days=6)
        if wk_close >= as_of or len(pts) < 7:
            continue
        pts.sort(key=lambda x: x[0])
        k = wk_open.isoformat()
        existing[k] = {
            'date': k,
            'open': round(pts[0][1], 2),
            'high': round(max(x[2] for x in pts), 2),
            'low': round(min(x[3] for x in pts), 2),
            'close': round(pts[-1][4], 2),
            'source': 'kraken_1d_resampled',
        }

    merged = [existing[k] for k in sorted(existing)]
    os.makedirs(os.path.dirname(ohlc_path) or '.', exist_ok=True)
    with open(ohlc_path, 'w', encoding='utf-8') as f:
        json.dump(
            {
                'schema': 'monday_closed_weekly_ohlc_v1',
                'bar_open_weekday': 'Monday',
                'count': len(merged),
                'series': merged,
            },
            f,
            separators=(',', ':'),
        )
    return len(merged)
