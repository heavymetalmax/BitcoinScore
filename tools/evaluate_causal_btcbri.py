#!/usr/bin/env python3
"""Evaluate Zero-Lookahead Causal BTCBRI on Historical Extremes & Forward Outcomes.

Constructs a strictly causal 3-axis BTCBRI (0-100) using ONLY data < date:
  - 40% Cost Basis (causal expanding percentile of MVRV)
  - 30% Trend Extension (causal expanding percentile of Mayer Multiple)
  - 30% Momentum / Miner Stress (closed weekly Cipher B score, fallback causal Puell pct)

Compares Causal BTCBRI vs Legacy BRI (which contains 2025 calibration leakage) at:
  1. Every historical cycle extreme in data/cycle_extremes.json + today (2026-11-24)
  2. Out-of-sample forward 365d return / drawdown buckets & Spearman IC (2017-20, 2021-25)
  3. Trailing-Peak Valuation Sleeve backtest (2018-2026 and 2021-2026)
"""
from __future__ import annotations

import bisect
import datetime
import json
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent


def causal_expanding_pct(series_by_date: dict[str, float]) -> dict[str, float]:
    buf: list[float] = []
    out: dict[str, float] = {}
    for d in sorted(series_by_date):
        v = series_by_date[d]
        rank = bisect.bisect_right(buf, v) / len(buf) if buf else 0.5
        out[d] = rank * 100.0
        bisect.insort(buf, v)
    return out


def main():
    scores = json.loads((ROOT / 'data/history/scores.json').read_text(encoding='utf-8'))
    extremes = json.loads((ROOT / 'data/cycle_extremes.json').read_text(encoding='utf-8')).get('extremes', [])
    cb_raw = json.loads((ROOT / 'data/history/cipherb_btcusdt_1w.json').read_text(encoding='utf-8'))
    cb_series = cb_raw.get('series', cb_raw) if isinstance(cb_raw, dict) else cb_raw
    data_now = json.loads((ROOT / 'data/data.json').read_text(encoding='utf-8'))

    # Closed weekly Cipher B scores available strictly AFTER bar close (week_open + 7d)
    cb_avail: list[tuple[str, float]] = []
    for r in cb_series:
        d = str(r.get('date', ''))[:10]
        ws = r.get('weekly_score')
        if not d or ws is None:
            continue
        dt = datetime.date.fromisoformat(d)
        if dt.weekday() != 3 and not r.get('is_closed_bar'):
            continue
        avail_d = (dt + datetime.timedelta(days=7)).isoformat()
        cb_avail.append((avail_d, float(ws)))
    cb_avail.sort(key=lambda x: x[0])

    # Daily raw series from scores.json
    mvrv_raw, mayer_raw, puell_raw, price_map, legacy_map = {}, {}, {}, {}, {}
    for r in scores:
        d = str(r.get('date', ''))[:10]
        if not d:
            continue
        if r.get('mvrv') is not None:
            mvrv_raw[d] = float(r['mvrv'])
        if r.get('mayer_multiple') is not None:
            mayer_raw[d] = float(r['mayer_multiple'])
        if r.get('puell') is not None:
            puell_raw[d] = float(r['puell'])
        if r.get('btc_price') is not None and float(r['btc_price']) > 0:
            price_map[d] = float(r['btc_price'])
        if r.get('final_score') is not None:
            legacy_map[d] = float(r['final_score'])

    mvrv_pct = causal_expanding_pct(mvrv_raw)
    mayer_pct = causal_expanding_pct(mayer_raw)
    puell_pct = causal_expanding_pct(puell_raw)

    # Compute Causal BTCBRI for every BTC-priced date
    causal_bri: dict[str, float] = {}
    causal_parts: dict[str, dict] = {}
    cb_idx = 0
    last_cb = None
    for d in sorted(price_map):
        while cb_idx < len(cb_avail) and cb_avail[cb_idx][0] <= d:
            last_cb = cb_avail[cb_idx][1]
            cb_idx += 1
        mv = mvrv_pct.get(d)
        my = mayer_pct.get(d)
        pu = puell_pct.get(d)
        mom = last_cb if last_cb is not None else pu
        if mv is not None and my is not None and mom is not None:
            val = round(0.40 * mv + 0.30 * my + 0.30 * mom, 1)
            causal_bri[d] = val
            causal_parts[d] = {
                'mvrv_pct': round(mv, 1),
                'mayer_pct': round(my, 1),
                'momentum_pct': round(mom, 1),
            }

    # 1. Historical Cycle Extremes + Today
    ext_rows = []
    for ex in sorted(extremes, key=lambda x: x['date']):
        d = ex['date'][:10]
        if causal_bri.get(d) is None and legacy_map.get(d) is None:
            continue
        t_low = str(ex['type']).lower()
        ext_rows.append({
            'date': d,
            'type': t_low,
            'major_cycle': bool(ex.get('major_cycle')),
            'label': f"{ex['type']} ({d[:7]})",
            'price': ex.get('price') or price_map.get(d),
            'causal_bri': causal_bri.get(d),
            'legacy_bri': legacy_map.get(d),
            'parts': causal_parts.get(d),
        })

    today_d = data_now.get('date', '2026-11-24')[:10]
    ext_rows.append({
        'date': today_d,
        'type': 'today',
        'label': f'Поточний зріз ({today_d})',
        'price': data_now.get('btc_price'),
        'causal_bri': causal_bri.get(today_d),
        'legacy_bri': data_now.get('bri_score', data_now.get('final_score')),
        'parts': causal_parts.get(today_d),
    })

    bots = [r['causal_bri'] for r in ext_rows if r['type'] == 'bottom' and r['causal_bri'] is not None]
    tops = [r['causal_bri'] for r in ext_rows if r['type'] == 'top' and r['causal_bri'] is not None]
    leg_bots = [r['legacy_bri'] for r in ext_rows if r['type'] == 'bottom' and r['legacy_bri'] is not None]
    leg_tops = [r['legacy_bri'] for r in ext_rows if r['type'] == 'top' and r['legacy_bri'] is not None]

    # 2. Forward 365d return & drawdown evaluation (2017-2025)
    max_p_d = datetime.date.fromisoformat(max(price_map))
    eval_rows = []
    for d in sorted(causal_bri):
        if d not in legacy_map or d not in price_map:
            continue
        d0 = datetime.date.fromisoformat(d)
        if d0 + datetime.timedelta(days=365) > max_p_d:
            continue
        fut = [
            price_map[(d0 + datetime.timedelta(days=k)).isoformat()]
            for k in range(1, 366)
            if (d0 + datetime.timedelta(days=k)).isoformat() in price_map
        ]
        if len(fut) < 300:
            continue
        p0 = price_map[d]
        p365 = price_map.get((d0 + datetime.timedelta(days=365)).isoformat(), fut[-1])
        ret365 = (p365 / p0 - 1.0) * 100.0
        dd365 = max(0.0, (p0 - min(fut)) / p0 * 100.0)
        eval_rows.append({
            'date': d,
            'causal_bri': causal_bri[d],
            'legacy_bri': legacy_map[d],
            'ret365': ret365,
            'dd365': dd365,
        })

    def regime_ic(sub_rows):
        cb_v = [r['causal_bri'] for r in sub_rows]
        lg_v = [r['legacy_bri'] for r in sub_rows]
        r365 = [r['ret365'] for r in sub_rows]
        d365 = [r['dd365'] for r in sub_rows]
        return {
            'days': len(sub_rows),
            'causal_spearman_ret365': round(float(stats.spearmanr(cb_v, r365).statistic), 4),
            'legacy_spearman_ret365': round(float(stats.spearmanr(lg_v, r365).statistic), 4),
            'causal_spearman_dd365': round(float(stats.spearmanr(cb_v, d365).statistic), 4),
            'legacy_spearman_dd365': round(float(stats.spearmanr(lg_v, d365).statistic), 4),
        }

    # 3. Trailing-peak backtest (entry <= 30, arm >= 75, exit <= peak - 15) on Causal vs Legacy BRI
    def bt_bri(bri_map: dict[str, float], start_d: str, fee: float = 0.005) -> dict:
        ds = [d for d in sorted(bri_map) if d >= start_d and d in price_map]
        cash, btc = 10000.0, 0.0
        armed, peak = False, 0.0
        eq = []
        closed = []
        entry_p = None
        for i, d in enumerate(ds):
            p = price_map[d]
            sc = bri_map[d]
            if btc == 0.0 and sc <= 30.0:
                btc = cash * (1.0 - fee) / p
                cash = 0.0
                armed = False
                peak = sc
                entry_p = p
            elif btc > 0.0:
                if sc >= 75.0:
                    armed = True
                if sc > peak:
                    peak = sc
                if armed and sc <= peak - 15.0:
                    cash = btc * p * (1.0 - fee)
                    btc = 0.0
                    armed = False
                    peak = 0.0
                    closed.append((p / entry_p * ((1.0 - fee) ** 2) - 1.0) * 100.0)
            eq.append(cash + btc * p)
        arr = np.array([10000.0] + eq, dtype=float)
        dd = float(np.max(1.0 - arr / np.maximum.accumulate(arr)) * 100.0)
        return {
            'multiple': round(float(arr[-1] / 10000.0), 2),
            'max_dd_pct': round(dd, 1),
            'closed_trades': len(closed),
            'open': int(btc > 0.0),
        }

    out = {
        'generated_at': datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        'formula': 'Causal BTCBRI = 0.40*MVRV_pct + 0.30*Mayer_pct + 0.30*CipherB_1W_closed (0% future leakage)',
        'extremes': ext_rows,
        'separation': {
            'causal_mean_bottom': round(float(np.mean(bots)), 1),
            'causal_mean_top': round(float(np.mean(tops)), 1),
            'causal_separation': round(float(np.mean(tops) - np.mean(bots)), 1),
            'legacy_mean_bottom': round(float(np.mean(leg_bots)), 1),
            'legacy_mean_top': round(float(np.mean(leg_tops)), 1),
            'legacy_separation': round(float(np.mean(leg_tops) - np.mean(leg_bots)), 1),
        },
        'regime_ic': {
            '2017_2020': regime_ic([r for r in eval_rows if r['date'] < '2021-01-01']),
            '2021_2025': regime_ic([r for r in eval_rows if r['date'] >= '2021-01-01']),
        },
        'bri_trailing_backtest': {
            '2018_2026': {
                'causal_bri': bt_bri(causal_bri, '2018-01-01'),
                'legacy_bri': bt_bri(legacy_map, '2018-01-01'),
            },
            '2021_2026': {
                'causal_bri': bt_bri(causal_bri, '2021-01-01'),
                'legacy_bri': bt_bri(legacy_map, '2021-01-01'),
            },
        },
    }
    for rel in ('data/causal_bri_extremes.json', 'web/causal_bri_extremes.json'):
        (ROOT / rel).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
