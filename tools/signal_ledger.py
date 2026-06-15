#!/usr/bin/env python3
"""Append a BUY_ZONE / SELL_ZONE entry to data/history/signal_ledger.json.

Reads data/data.json written by the scraper. Writes nothing when:
  - final_score is neutral (25 < score < 75)
  - today's date already exists in the ledger
"""
import json
import os

BUY_THRESHOLD  = 25
SELL_THRESHOLD = 75
LEDGER_PATH    = 'data/history/signal_ledger.json'
DATA_PATH      = 'data/data.json'


def _load(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def run():
    with open(DATA_PATH) as f:
        data = json.load(f)

    score = data.get('final_score')
    if score is None:
        print('signal_ledger: final_score missing, skipping')
        return

    if BUY_THRESHOLD < score < SELL_THRESHOLD:
        print(f'signal_ledger: score={score} neutral, no entry')
        return

    zone = 'BUY_ZONE' if score <= BUY_THRESHOLD else 'SELL_ZONE'
    date = (data.get('timestamp') or '')[:10]

    ledger = _load(LEDGER_PATH)
    if any(e.get('date') == date for e in ledger):
        print(f'signal_ledger: {date} already recorded, skipping')
        return

    sig = data.get('signal') or {}
    ph  = data.get('phase')  or {}

    entry = {
        'date':       date,
        'score':      score,
        'btc_price':  data.get('btc_price'),
        'signal':     zone,
        'phase':      ph.get('phase')      if isinstance(ph, dict) else None,
        'conviction': sig.get('conviction') if isinstance(sig, dict) else None,
        'flag':       sig.get('flag')       if isinstance(sig, dict) else None,
    }

    ledger.append(entry)
    ledger.sort(key=lambda e: e['date'])

    with open(LEDGER_PATH, 'w') as f:
        json.dump(ledger, f, indent=2)

    print(f'signal_ledger: recorded {zone} on {date} (score={score}, price={entry["btc_price"]})')


if __name__ == '__main__':
    run()
