#!/usr/bin/env python3
"""Evaluate Production Causal Core (Valuation + D1 Allocator) on Historical Extremes.

Evaluates the ACTUAL production causal engine (zero future leakage, zero ad-hoc
manual composite):
  1. Causal Cost-Basis Valuation Score (0-100): equal-weighted causal expanding
     percentile of MVRV, Puell, and CVDD strictly < date (catches bottoms cleanly).
  2. Causal D1 Dual-Sleeve Target Allocation (0% / 50% / 100%) on closed weekly
     OHLC (`50% SMA40w + 50% Cipher B 1W SMA3 + MVRV Value-Veto <= 25%`) at each
     extreme AND 4w / 8w after tops — showing how trend/momentum exits mature tops
     like 2025-10 without waiting for a static 0-100 thermometer >= 85.
  3. Out-of-sample forward 365d Spearman IC of Causal Valuation vs Legacy BRI.
"""
from __future__ import annotations

import bisect
import datetime
import json
import sys
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.causal_core import _load_daily_mvrv_ranks, compute_cipherb_wt_on_ohlc
from scraper.ohlc_store import load_closed_weekly_ohlc


def causal_expanding_pct(series_by_date: dict[str, float]) -> dict[str, float]:
    buf: list[float] = []
    out: dict[str, float] = {}
    for d in sorted(series_by_date):
        v = series_by_date[d]
        rank = bisect.bisect_right(buf, v) / len(buf) if buf else 0.5
        out[d] = rank * 100.0
        bisect.insort(buf, v)
    return out


def replay_d1_weekly_history(as_of: datetime.date) -> list[dict]:
    weeks = load_closed_weekly_ohlc(as_of_date=as_of, ohlc_path=str(ROOT / 'data/history/btc_weekly_ohlc.json'))
    wt1, wt2 = compute_cipherb_wt_on_ohlc(weeks)
    mvrv_ranks = _load_daily_mvrv_ranks(scores_path=str(ROOT / 'data/history/scores.json'), as_of_date=as_of)

    closes = [w['close'] for w in weeks]
    s1_in, s2_in = False, False
    mv_idx = 0
    last_mv = None
    out = []
    for i, w in enumerate(weeks):
        wk_close = w['week_close']
        while mv_idx < len(mvrv_ranks) and mvrv_ranks[mv_idx][0] <= wk_close:
            last_mv = mvrv_ranks[mv_idx][1]
            mv_idx += 1
        if i >= 39:
            sma40 = sum(closes[i - 39:i + 1]) / 40.0
            if not s1_in and w['close'] > sma40:
                s1_in = True
            elif s1_in and w['close'] < sma40:
                s1_in = False
        sp = (wt1[i] - wt2[i]) if (wt1[i] is not None and wt2[i] is not None) else None
        if sp is not None:
            if not s2_in and sp >= 2.0:
                s2_in = True
            elif s2_in and sp <= -2.0:
                if last_mv is None or last_mv > 0.25:
                    s2_in = False
        out.append({
            'avail_date': (wk_close + datetime.timedelta(days=1)).isoformat(),
            'week_open': w['date'],
            'sma40_in': s1_in,
            'cb_in': s2_in,
            'd1_alloc_pct': (50 if s1_in else 0) + (50 if s2_in else 0),
            'wt_spread': round(float(sp), 1) if sp is not None else None,
        })
    return out


def d1_state_at(d1_hist: list[dict], target_date: str) -> dict | None:
    best = None
    for row in d1_hist:
        if row['avail_date'] <= target_date:
            best = row
        else:
            break
    return best


def main():
    scores = json.loads((ROOT / 'data/history/scores.json').read_text(encoding='utf-8'))
    extremes = json.loads((ROOT / 'data/cycle_extremes.json').read_text(encoding='utf-8')).get('extremes', [])
    data_now = json.loads((ROOT / 'data/data.json').read_text(encoding='utf-8'))
    today_d = str(data_now.get('date') or scores[-1]['date'])[:10]

    mvrv_raw, puell_raw, cvdd_raw, price_map, legacy_map = {}, {}, {}, {}, {}
    for r in scores:
        d = str(r.get('date', ''))[:10]
        if not d:
            continue
        if r.get('mvrv') is not None:
            mvrv_raw[d] = float(r['mvrv'])
        if r.get('puell') is not None:
            puell_raw[d] = float(r['puell'])
        if r.get('cvdd_ratio') is not None:
            cvdd_raw[d] = float(r['cvdd_ratio'])
        if r.get('btc_price') is not None and float(r['btc_price']) > 0:
            price_map[d] = float(r['btc_price'])
        if r.get('final_score') is not None:
            legacy_map[d] = float(r['final_score'])

    mvrv_pct = causal_expanding_pct(mvrv_raw)
    puell_pct = causal_expanding_pct(puell_raw)
    cvdd_pct = causal_expanding_pct(cvdd_raw)

    # Actual production Causal Cost-Basis Valuation Score (MVRV + Puell + CVDD)
    causal_val: dict[str, float] = {}
    causal_parts: dict[str, dict] = {}
    for d in sorted(price_map):
        comps = []
        p_d = {}
        if d in mvrv_pct:
            comps.append(mvrv_pct[d])
            p_d['mvrv_pct'] = round(mvrv_pct[d], 1)
        if d in puell_pct:
            comps.append(puell_pct[d])
            p_d['puell_pct'] = round(puell_pct[d], 1)
        if d in cvdd_pct:
            comps.append(cvdd_pct[d])
            p_d['cvdd_pct'] = round(cvdd_pct[d], 1)
        if comps:
            causal_val[d] = round(sum(comps) / len(comps), 1)
            causal_parts[d] = p_d

    d1_hist = replay_d1_weekly_history(datetime.date.fromisoformat(today_d))

    ext_rows = []
    for ex in sorted(extremes, key=lambda x: x['date']):
        d = ex['date'][:10]
        if causal_val.get(d) is None and legacy_map.get(d) is None:
            continue
        t_low = str(ex['type']).lower()
        st_now = d1_state_at(d1_hist, d)
        d_plus_8w = (datetime.date.fromisoformat(d) + datetime.timedelta(days=56)).isoformat()
        st_8w = d1_state_at(d1_hist, d_plus_8w) if d_plus_8w <= today_d else None
        ext_rows.append({
            'date': d,
            'type': t_low,
            'major_cycle': bool(ex.get('major_cycle')),
            'label': f"{ex['type']} ({d[:7]})",
            'price': ex.get('price') or price_map.get(d),
            'causal_valuation': causal_val.get(d),
            'causal_bri': causal_val.get(d),
            'legacy_bri': legacy_map.get(d),
            'd1_alloc_pct': st_now['d1_alloc_pct'] if st_now else None,
            'd1_alloc_plus8w_pct': st_8w['d1_alloc_pct'] if st_8w else None,
            'parts': causal_parts.get(d),
        })

    st_today = d1_state_at(d1_hist, today_d)
    ext_rows.append({
        'date': today_d,
        'type': 'today',
        'label': f'Поточний зріз ({today_d})',
        'price': data_now.get('btc_price'),
        'causal_valuation': (data_now.get('causal_valuation') or {}).get('valuation_score', causal_val.get(today_d)),
        'causal_bri': (data_now.get('causal_valuation') or {}).get('valuation_score', causal_val.get(today_d)),
        'legacy_bri': data_now.get('bri_score', data_now.get('final_score')),
        'd1_alloc_pct': st_today['d1_alloc_pct'] if st_today else None,
        'd1_alloc_plus8w_pct': None,
        'parts': causal_parts.get(today_d),
    })

    bots = [r['causal_valuation'] for r in ext_rows if r['type'] == 'bottom' and r['causal_valuation'] is not None]
    tops = [r['causal_valuation'] for r in ext_rows if r['type'] == 'top' and r['causal_valuation'] is not None]
    leg_bots = [r['legacy_bri'] for r in ext_rows if r['type'] == 'bottom' and r['legacy_bri'] is not None]
    leg_tops = [r['legacy_bri'] for r in ext_rows if r['type'] == 'top' and r['legacy_bri'] is not None]

    max_p_d = datetime.date.fromisoformat(max(price_map))
    eval_rows = []
    for d in sorted(causal_val):
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
        eval_rows.append({
            'date': d,
            'causal_val': causal_val[d],
            'legacy_bri': legacy_map[d],
            'ret365': (p365 / p0 - 1.0) * 100.0,
            'dd365': max(0.0, (p0 - min(fut)) / p0 * 100.0),
        })

    def regime_ic(sub_rows):
        cv = [r['causal_val'] for r in sub_rows]
        lg = [r['legacy_bri'] for r in sub_rows]
        r365 = [r['ret365'] for r in sub_rows]
        d365 = [r['dd365'] for r in sub_rows]
        return {
            'days': len(sub_rows),
            'causal_spearman_ret365': round(float(stats.spearmanr(cv, r365).statistic), 4),
            'legacy_spearman_ret365': round(float(stats.spearmanr(lg, r365).statistic), 4),
            'causal_spearman_dd365': round(float(stats.spearmanr(cv, d365).statistic), 4),
            'legacy_spearman_dd365': round(float(stats.spearmanr(lg, d365).statistic), 4),
        }

    out = {
        'generated_at': datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        'formula': 'Production Causal Valuation = mean causal expanding rank of (MVRV, Puell, CVDD) + D1 Dual-Sleeve allocation (50% SMA40w + 50% Cipher B 1W & MVRV Veto)',
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
    }
    for rel in ('data/causal_bri_extremes.json', 'web/causal_bri_extremes.json'):
        (ROOT / rel).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
