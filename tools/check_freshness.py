"""Check if data/data.json is fresh for today.

Usage:
  python3 tools/check_freshness.py [path_or_dash]

Exits with:
  0: Data is fresh for today (no scraping needed).
  1: Data is stale or missing (scraping needed).
"""
import datetime
import json
import sys
from pathlib import Path


def is_fresh(data_source) -> bool:
    try:
        if data_source == '-':
            d = json.load(sys.stdin)
        else:
            p = Path(data_source)
            if not p.exists():
                print(f"Freshness: {p} does not exist -> STALE")
                return False
            with open(p, 'r', encoding='utf-8') as f:
                d = json.load(f)
    except Exception as e:
        print(f"Freshness: Error reading data: {e} -> STALE")
        return False

    ts = (d.get('timestamp') or '')[:10]
    today = datetime.date.today().isoformat()

    nupl = d.get('nupl')
    mvrv = d.get('mvrv_z_score')
    metrics = d.get('metrics') or {}
    nupl_meta = metrics.get('nupl') or {}
    mvrv_meta = metrics.get('mvrv') or {}

    nupl_fresh = (
        nupl is not None
        and not nupl_meta.get('is_fallback', False)
        and (nupl_meta.get('updated') or '')[:10] == today
    )
    mvrv_fresh = (
        mvrv is not None
        and not mvrv_meta.get('is_fallback', False)
        and (mvrv_meta.get('updated') or '')[:10] == today
    )

    if ts == today and nupl_fresh and mvrv_fresh:
        print(f"Freshness: Data is fresh for today ({today}) — ts={ts}, nupl={nupl}, mvrv={mvrv}")
        return True

    print(f"Freshness: Data is STALE (today={today}, ts={ts}, nupl_fresh={nupl_fresh}, mvrv_fresh={mvrv_fresh})")
    return False


if __name__ == '__main__':
    source = sys.argv[1] if len(sys.argv) > 1 else 'data/data.json'
    sys.exit(0 if is_fresh(source) else 1)
