#!/usr/bin/env python3
"""Append a BUY_ZONE / SELL_ZONE entry to data/history/signal_ledger.json.

Thresholds learned from ideal-trader grid search (tools/ideal_trader_test.py):
  BUY  : score ≤ 15, confirmed by ≥ 14 consecutive days in buy zone
  SELL : score ≥ 80, confirmed by w_top ≥ 0.35 (phase must lean TOP)

Writes nothing when score is neutral or filters not met.
"""
import json
import os
import datetime

BUY_THRESHOLD  = 15   # learned: 15 beats 25 — avoids falling-knife entries
SELL_THRESHOLD = 80   # learned: 80+w_top filter prevents premature exits
MIN_TIZ_DAYS   = 14   # minimum consecutive days in buy zone before BUY signal
MIN_WTOP_SELL  = 0.35 # w_top must be ≥ this to confirm SELL signal

LEDGER_PATH    = 'data/history/signal_ledger.json'
DATA_PATH      = 'data/data.json'
SCORES_PATH    = 'data/history/scores.json'


def _load(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def _consecutive_buy_days(ledger: list, today: str) -> int:
    """Count consecutive days ending on today where signal == BUY_ZONE."""
    entries = sorted(ledger, key=lambda e: e['date'])
    count = 0
    for e in reversed(entries):
        if e['date'] >= today:
            continue
        if e.get('signal') == 'BUY_ZONE':
            count += 1
        else:
            break
    return count


def run():
    with open(DATA_PATH) as f:
        data = json.load(f)

    score = data.get('final_score')
    if score is None:
        print('signal_ledger: final_score missing, skipping')
        return

    date  = (data.get('timestamp') or '')[:10]
    w_top = data.get('v3_w_top') or 0.0

    # Determine raw zone from score only
    if score <= BUY_THRESHOLD:
        raw_zone = 'BUY_ZONE'
    elif score >= SELL_THRESHOLD:
        raw_zone = 'SELL_ZONE'
    else:
        print(f'signal_ledger: score={score} neutral, no entry')
        return

    ledger = _load(LEDGER_PATH)
    if any(e.get('date') == date for e in ledger):
        print(f'signal_ledger: {date} already recorded, skipping')
        return

    # Apply learned filters
    if raw_zone == 'BUY_ZONE':
        # Count how many consecutive prior days were also in buy zone
        tiz = _consecutive_buy_days(ledger, date)
        if tiz < MIN_TIZ_DAYS:
            print(f'signal_ledger: BUY_ZONE score={score} but tiz={tiz}<{MIN_TIZ_DAYS}d — too early, recording as WATCH')
            zone = 'BUY_WATCH'
        else:
            zone = 'BUY_ZONE'
    else:
        # SELL requires w_top confirmation
        if w_top < MIN_WTOP_SELL:
            print(f'signal_ledger: SELL_ZONE score={score} but w_top={w_top:.2f}<{MIN_WTOP_SELL} — not confirmed, recording as WATCH')
            zone = 'SELL_WATCH'
        else:
            zone = 'SELL_ZONE'

    sig = data.get('signal') or {}
    ph  = data.get('phase')  or {}

    entry = {
        'date':       date,
        'score':      score,
        'btc_price':  data.get('btc_price'),
        'signal':     zone,
        'w_top':      round(w_top, 3),
        'phase':      ph.get('phase')      if isinstance(ph, dict) else None,
        'conviction': sig.get('conviction') if isinstance(sig, dict) else None,
        'flag':       sig.get('flag')       if isinstance(sig, dict) else None,
    }

    ledger.append(entry)
    ledger.sort(key=lambda e: e['date'])

    with open(LEDGER_PATH, 'w') as f:
        json.dump(ledger, f, indent=2)

    print(f'signal_ledger: recorded {zone} on {date} (score={score}, w_top={w_top:.2f}, price={entry["btc_price"]})')


if __name__ == '__main__':
    run()
