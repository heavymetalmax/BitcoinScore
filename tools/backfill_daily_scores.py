#!/usr/bin/env python3
"""Reconstruct daily composite scores from 2016-01-01 → today using backfilled history.

Data sources used:
  On-chain (daily): NUPL, MVRV, Puell, RHODL, aSOPR from *_history.json
  Tech (weekly→daily): CipherB from cipherb_btcusdt_1w.json (week containing each date)
  Excluded: CVDD (raw price, no BTC price series), ETF Flows (2024+ only),
            Mayer Multiple (monthly only, limited range), Yield Curve, M2 YoY

Purpose:
  - TiZ calibration: count actual days composite ≤ 40 in 2018 and 2022 cycles
  - Regime detection accuracy: verify bottom/neutral/top across full cycles
  - v2 scoring backtest

Caveats:
  - Adaptive calibration uses FULL history (look-ahead bias); for research only
  - CipherB weekly → daily: same week value used for all 7 days
  - Tech group is CipherB-dominant (other tech metrics unavailable historically)

Output: data/history/backfill_scores.json

Usage:
    python tools/backfill_daily_scores.py
    python tools/backfill_daily_scores.py --from 2017-01-01 --to 2023-12-31
"""
import sys, os, json, datetime, math, argparse
sys.path.insert(0, '.')

from scraper.scoring import (
    map_nupl, map_mvrv, map_asopr, map_rhodl, weighted_score,
    _load_metric_history,
)
from scraper.scoring_v2 import map_puell, OC_BOTTOM, OC_TOP, OC_NEUTRAL, TECH_BOTTOM, TECH_TOP, TECH_NEUTRAL, BOTTOM_THRESHOLD, TOP_THRESHOLD

OUT_PATH = 'data/history/backfill_scores.json'

# ── Load history series into date→value dicts ────────────────────────────────
def load_series(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict) and 'series' in d:
            return {r[0][:10]: r[1] for r in d['series'] if r[1] is not None}
    except Exception:
        pass
    return {}

def load_cipherb_weekly():
    """Returns date→weekly_score mapping (each day maps to its week's score)."""
    try:
        with open('data/history/cipherb_btcusdt_1w.json', encoding='utf-8') as f:
            raw = json.load(f)
        result = {}
        candles = raw['series']
        for i, c in enumerate(candles):
            week_start = datetime.date.fromtimestamp(c['timestamp'])
            week_end = (datetime.date.fromtimestamp(candles[i+1]['timestamp'])
                        if i + 1 < len(candles) else week_start + datetime.timedelta(days=7))
            score = c.get('weekly_score')
            if score is None:
                continue
            d = week_start
            while d < week_end:
                result[d.isoformat()] = score
                d += datetime.timedelta(days=1)
        return result
    except Exception as e:
        print(f'  warn: CipherB weekly load failed: {e}')
        return {}

def nearest(data, date_str, window=5):
    """Find nearest value within ±window days."""
    if date_str in data:
        return data[date_str]
    target = datetime.date.fromisoformat(date_str)
    best_v, best_dist = None, window + 1
    for k, v in data.items():
        try:
            dist = abs((datetime.date.fromisoformat(k) - target).days)
        except ValueError:
            continue
        if dist < best_dist:
            best_dist, best_v = dist, v
    return best_v

# ── Score computation ─────────────────────────────────────────────────────────
def build_sm_for_date(date_str, nupl_h, mvrv_h, puell_h, rhodl_h, asopr_h, cb_h):
    """Build slider_map for a given date from historical series."""
    nupl_v  = nearest(nupl_h,  date_str)
    mvrv_v  = nearest(mvrv_h,  date_str)
    puell_v = nearest(puell_h, date_str)
    rhodl_v = nearest(rhodl_h, date_str)
    asopr_v = nearest(asopr_h, date_str)
    cb_v    = cb_h.get(date_str)

    return {
        'nupl':          map_nupl(nupl_v),
        'mvrv_z_score':  map_mvrv(mvrv_v),
        'puell':         map_puell(puell_v),
        'rhodl_ratio':   map_rhodl(rhodl_v),
        'asopr':         map_asopr(asopr_v),
        'cipherb':       round(cb_v) if cb_v is not None else None,
        # tech-only metrics excluded (no daily history)
        'mayer_multiple':    None,
        'fear_greed':        None,
        'etf_flows':         None,
        'yield_curve_spread': None,
        'm2_yoy':            None,
    }, {
        'nupl': nupl_v, 'mvrv': mvrv_v, 'puell': puell_v,
        'rhodl': rhodl_v, 'asopr': asopr_v, 'cipherb_ws': cb_v,
    }

# OC weights without Puell (v1 neutral for prelim pass)
OC_V1 = {'nupl': 0.30, 'mvrv_z_score': 0.20, 'rhodl_ratio': 0.20, 'cvdd_ratio': 0.15, 'asopr': 0.15}
# Tech reduced to CipherB-only (others None → renorm)
TECH_ONLY_CB = {'cipherb': 1.0}

def compute_day(date_str, sm):
    """Return (v1_final, v2_final, regime, oc_avg, tech_avg) for a date."""
    # v1 on-chain (without Puell, without CVDD)
    oc_v1 = weighted_score(OC_V1, sm)
    # tech: CipherB dominant
    tech = weighted_score(TECH_ONLY_CB, sm)

    def blend(oc, t, w):
        parts = [(oc, w), (t, 1-w)]
        vals = [(v, wt) for v, wt in parts if v is not None]
        if not vals: return None
        s = sum(v*wt for v,wt in vals)
        tw = sum(wt for _,wt in vals)
        return round(s/tw)

    v1_final = blend(oc_v1, tech, 0.50)

    # Regime detection on v1
    if v1_final is None:
        return v1_final, v1_final, 'neutral', oc_v1, tech

    if v1_final <= BOTTOM_THRESHOLD:
        regime = 'bottom'
        oc_w, tech_w = OC_BOTTOM, TECH_BOTTOM
    elif v1_final >= TOP_THRESHOLD:
        regime = 'top'
        oc_w, tech_w = OC_TOP, TECH_TOP
    else:
        regime = 'neutral'
        oc_w, tech_w = OC_NEUTRAL, TECH_NEUTRAL

    oc_v2   = weighted_score(oc_w,   sm)
    tech_v2 = weighted_score(tech_w, sm)
    v2_final = blend(oc_v2, tech_v2, 0.50)

    return v1_final, v2_final, regime, oc_v2, tech_v2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from', dest='date_from', default='2016-01-01')
    parser.add_argument('--to',   dest='date_to',
                        default=(datetime.date.today()).isoformat())
    args = parser.parse_args()

    print('Loading history files…')
    nupl_h  = load_series('data/history/nupl_history.json')
    mvrv_h  = load_series('data/history/mvrv_history.json')
    puell_h = load_series('data/history/puell_history.json')
    rhodl_h = load_series('data/history/rhodl_history.json')
    asopr_h = load_series('data/history/asopr_history.json')
    cb_h    = load_cipherb_weekly()
    print(f'  NUPL={len(nupl_h)} MVRV={len(mvrv_h)} Puell={len(puell_h)} '
          f'RHODL={len(rhodl_h)} aSOPR={len(asopr_h)} CipherB={len(cb_h)}')

    # Also load daily_vector.json for post-2026-04-27 real scores
    dv_scores = {}
    try:
        with open('data/history/daily_vector.json', encoding='utf-8') as f:
            for row in json.load(f):
                dv_scores[row['date']] = row.get('final_score')
    except Exception:
        pass

    date = datetime.date.fromisoformat(args.date_from)
    end  = datetime.date.fromisoformat(args.date_to)
    series = []
    tiz_days_running = 0  # rolling count for TiZ analysis

    print(f'Computing scores {args.date_from} → {args.date_to}…')
    while date <= end:
        ds = date.isoformat()

        # Use real composite from daily_vector if available
        if ds in dv_scores and dv_scores[ds] is not None:
            date += datetime.timedelta(days=1)
            continue  # skip — real data covers this period

        sm, raw = build_sm_for_date(ds, nupl_h, mvrv_h, puell_h, rhodl_h, asopr_h, cb_h)
        v1, v2, regime, oc, tech = compute_day(ds, sm)

        # Rolling TiZ counter (last 180 days, ≤40)
        # Approximate: use v1 as proxy for zone membership
        if v1 is not None and v1 <= BOTTOM_THRESHOLD:
            tiz_days_running = min(tiz_days_running + 1, 180)
        else:
            tiz_days_running = 0

        series.append({
            'date':        ds,
            'v1_final':    v1,
            'v2_final':    v2,
            'regime':      regime,
            'oc_score':    oc,
            'tech_score':  tech,
            'tiz_days':    tiz_days_running if (v1 is not None and v1 <= BOTTOM_THRESHOLD) else 0,
            'raw':         raw,
            'mapped':      {k: v for k, v in sm.items() if v is not None},
            'source':      'backfill',
        })
        date += datetime.timedelta(days=1)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out = {
        'meta': {
            'generated':  datetime.datetime.utcnow().isoformat() + 'Z',
            'date_from':  args.date_from,
            'date_to':    args.date_to,
            'n':          len(series),
            'caveats':    [
                'Adaptive calibration uses full history (look-ahead bias) — research use only',
                'Tech group: CipherB only (weekly→daily interp); Mayer/ETF/YieldCurve/M2 excluded',
                'CVDD excluded (raw price, no BTC price series for ratio)',
                'TiZ is consecutive (not rolling) for simplicity in backfill',
            ],
        },
        'series': series,
    }
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f)
    print(f'Wrote {OUT_PATH}  ({len(series)} days)')

    # ── Quick analysis ───────────────────────────────────────────────────────
    print('\n══ BOTTOM ZONE EPISODES (v1 ≤ 40) ══')
    in_zone = False
    episodes = []
    ep_start = ep_min = ep_max_tiz = None
    for r in series:
        v = r['v1_final']
        if v is None:
            continue
        if v <= 40 and not in_zone:
            in_zone = True
            ep_start = r['date']
            ep_min = v
            ep_max_tiz = 0
        elif v <= 40 and in_zone:
            ep_min = min(ep_min, v)
            ep_max_tiz = max(ep_max_tiz, r['tiz_days'])
        elif v > 40 and in_zone:
            in_zone = False
            episodes.append((ep_start, r['date'], ep_min, ep_max_tiz))
    if in_zone:
        episodes.append((ep_start, series[-1]['date'], ep_min, ep_max_tiz))

    for ep_s, ep_e, ep_min_v, ep_tiz in episodes:
        d1 = datetime.date.fromisoformat(ep_s)
        d2 = datetime.date.fromisoformat(ep_e)
        days = (d2 - d1).days
        if days >= 7:
            print(f'  {ep_s} → {ep_e}  ({days}d)  min_score={ep_min_v}  max_tiz={ep_tiz}')

    print('\n══ REGIME DISTRIBUTION ══')
    from collections import Counter
    regimes = Counter(r['regime'] for r in series if r['v1_final'] is not None)
    total = sum(regimes.values())
    for k, v in regimes.most_common():
        print(f'  {k:<8}: {v:4d} days  ({v/total*100:.1f}%)')


if __name__ == '__main__':
    main()
