#!/usr/bin/env python3
"""Prospective Out-of-Sample Evaluator for data/pit_ledger.jsonl.

Checks each immutable Point-in-Time (PiT) snapshot recorded in
`data/pit_ledger.jsonl` against realized future BTC prices in
`data/history/scores.json` at 13-week (+91d) and 52-week (+365d) horizons,
and scores 13-week tail-risk probability calibration (Brier) once matured.

Usage:
    python3 tools/eval_pit_ledger.py
"""
from __future__ import annotations

import datetime
import json
import os
import sys

PIT_PATH = 'data/pit_ledger.jsonl'
SCORES_PATH = 'data/history/scores.json'


def evaluate_pit_ledger(
    pit_path: str = PIT_PATH,
    scores_path: str = SCORES_PATH,
) -> dict:
    if not os.path.exists(pit_path):
        print(f'PiT Ledger: {pit_path} not found — 0 snapshots.')
        return {'total': 0, 'matured_13w': 0, 'matured_52w': 0}

    snaps: dict[str, dict] = {}
    with open(pit_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                d = str(rec.get('as_of_date', ''))[:10]
                if d:
                    snaps[d] = rec
            except json.JSONDecodeError:
                continue

    prices: dict[datetime.date, float] = {}
    if os.path.exists(scores_path):
        for row in json.load(open(scores_path, encoding='utf-8')):
            d = str(row.get('date', ''))[:10]
            p = row.get('btc_price')
            if d and p is not None and float(p) > 0:
                prices[datetime.date.fromisoformat(d)] = float(p)

    sorted_dates = sorted(snaps)
    max_price_date = max(prices) if prices else None
    matured_13w = []
    matured_52w = []
    brier_terms = []

    for d_str in sorted_dates:
        rec = snaps[d_str]
        dt = datetime.date.fromisoformat(d_str)
        p0 = rec.get('btc_price') or prices.get(dt)
        if not p0 or p0 <= 0 or max_price_date is None:
            continue

        dt_13w = dt + datetime.timedelta(days=91)
        if max_price_date >= dt_13w and dt_13w in prices:
            p13 = prices[dt_13w]
            win_prices = [prices[k] for k in prices if dt < k <= dt_13w]
            dd25 = 1 if (win_prices and min(win_prices) <= p0 * 0.75) else 0
            ret13 = (p13 / p0 - 1.0) * 100.0
            matured_13w.append({'date': d_str, 'ret13_pct': ret13, 'dd25': dd25})
            prob_tr = rec.get('forward_risk_13w')
            if prob_tr is not None:
                brier_terms.append((float(prob_tr) / 100.0 - dd25) ** 2)

        dt_52w = dt + datetime.timedelta(days=365)
        if max_price_date >= dt_52w and dt_52w in prices:
            p52 = prices[dt_52w]
            matured_52w.append({'date': d_str, 'ret52_pct': (p52 / p0 - 1.0) * 100.0})

    earliest_13w = (
        (datetime.date.fromisoformat(sorted_dates[0]) + datetime.timedelta(days=91)).isoformat()
        if sorted_dates else None
    )
    earliest_52w = (
        (datetime.date.fromisoformat(sorted_dates[0]) + datetime.timedelta(days=365)).isoformat()
        if sorted_dates else None
    )
    brier = (sum(brier_terms) / len(brier_terms)) if brier_terms else None

    print(f'PiT Ledger Audit: {len(sorted_dates)} snapshots ({sorted_dates[0] if sorted_dates else "—"} .. {sorted_dates[-1] if sorted_dates else "—"})')
    print(f'  Matured 13w (+91d):  {len(matured_13w)}  (earliest maturity: {earliest_13w or "—"})')
    print(f'  Matured 52w (+365d): {len(matured_52w)}  (earliest maturity: {earliest_52w or "—"})')
    if brier is not None:
        print(f'  Realized 13w Tail-Risk Brier: {brier:.4f}')

    return {
        'total': len(sorted_dates),
        'first_date': sorted_dates[0] if sorted_dates else None,
        'last_date': sorted_dates[-1] if sorted_dates else None,
        'matured_13w': len(matured_13w),
        'matured_52w': len(matured_52w),
        'earliest_13w_maturity': earliest_13w,
        'earliest_52w_maturity': earliest_52w,
        'brier_13w': round(brier, 5) if brier is not None else None,
    }


def main():
    evaluate_pit_ledger()


if __name__ == '__main__':
    sys.exit(0 if main() is None else 0)
