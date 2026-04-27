#!/usr/bin/env python3
import json
from datetime import datetime
from pathlib import Path

IN = Path('data/history/m2.json')
OUT = Path('data/history/m2_mom.json')

def month_key(ts):
    dt = datetime.fromisoformat(ts.replace('Z',''))
    return dt.year, dt.month

if not IN.exists():
    print('input not found:', IN)
    raise SystemExit(1)

with IN.open('r', encoding='utf-8') as f:
    arr = json.load(f)

# sort by timestamp
arr = sorted(arr, key=lambda x: x.get('timestamp'))
# pick last entry per month
last_per_month = {}
for item in arr:
    ts = item.get('timestamp')
    key = month_key(ts)
    last_per_month[key] = item

# produce ordered months
months = sorted(last_per_month.keys())
out = []
prev_val = None
for key in months:
    item = last_per_month[key]
    m2 = item.get('m2') or {}
    val = m2.get('usd') if isinstance(m2, dict) else None
    # fallback to numeric 'value'
    if val is None:
        try:
            val = float(m2.get('value')) if isinstance(m2, dict) and 'value' in m2 else None
        except Exception:
            val = None
    mom = None
    if prev_val is not None and prev_val != 0 and val is not None:
        mom = (val - prev_val) / prev_val * 100.0
    out.append({
        'month': f"{key[0]:04d}-{key[1]:02d}",
        'timestamp': item.get('timestamp'),
        'm2_usd': val,
        'm2_mom_pct': round(mom, 4) if mom is not None else None,
    })
    prev_val = val

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open('w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print('wrote', OUT, 'entries=', len(out))
