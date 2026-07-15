#!/usr/bin/env python3
"""Regenerate data/history/scores.json using the clean V3 scoring engine.

Iterates every date from START_DATE to today, builds raw metrics causally
from loaded series files, and calls compute_score() + orchestrate() — the
same code path as the live scraper.

TiZ is handled correctly: scores_history is accumulated in memory and passed
to each compute_score() call, so TiZ sees only past (not future) scores.

Usage:
    python tools/backfill_v3_scores.py
    python tools/backfill_v3_scores.py --from 2022-01-01
    python tools/backfill_v3_scores.py --dry-run      # print first 10 rows, no write
"""
import sys, os, json, datetime, argparse
sys.path.insert(0, '.')

from tools.backtest import (
    load_data, compute_at, _prev_scores, nearest_strict, CORE_REQUIRED
)
from scraper.score import compute_score
from scraper.orchestrator import orchestrate
from scraper.utils import write_json

START_DATE  = datetime.date(2018, 1, 1)
OUT_PATH    = 'data/history/scores.json'


def build_raw(td, series):
    """Reconstruct raw metrics dict for a given date (same as v3_at)."""
    r    = compute_at(td, series)
    # Skip dates where core metrics are absent
    if not r['scores']:
        return None, None

    dxy_val, _     = nearest_strict(series.get('dxy', []),          td)
    fr_val, _      = nearest_strict(series.get('funding_rate', []), td)
    pi_val, _      = nearest_strict(series.get('pi_gap_pct', []),   td)
    wk_score, _    = nearest_strict(series.get('cipherb_weekly', []), td)
    wk_fb, _       = nearest_strict(series.get('cipherb_weekly_fb', []), td)
    cb_daily, _    = nearest_strict(series.get('cipherb', []),       td)

    if wk_score is not None:
        cb_dict = {'weekly_score': wk_score, 'daily_score': cb_daily, 'fast_bearish_div': bool(wk_fb)}
    elif cb_daily is not None:
        cb_dict = {'weekly_score': cb_daily, 'daily_score': cb_daily, 'fast_bearish_div': False}
    else:
        cb_dict = None

    raw = {
        'nupl':           r['raw'].get('nupl'),
        'mvrv':           r['raw'].get('mvrv'),
        'rhodl_ratio':    r['raw'].get('rhodl_ratio'),
        'cvdd_ratio':     r['raw'].get('cvdd_ratio'),
        'asopr':          r['raw'].get('asopr'),
        'puell_multiple': r['raw'].get('puell_multiple'),
        'mayer_multiple': r['raw'].get('mayer_multiple'),
        'fear_greed':     r['raw'].get('fear_greed'),
        'm2_mom':         r['raw'].get('m2_yoy'),
        'yield_curve':    r['raw'].get('yield_curve_spread'),
        'lth_supply_pct': r['raw'].get('lth_supply'),
        'dxy':            dxy_val,
        'funding_rate':   fr_val,
        'pi_cycle':       pi_val,
        'cipherb':        cb_dict,
        'etf_flows':      r['raw'].get('etf_flows'),
    }
    return raw, r['raw'].get('btc_price')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from', dest='start', default=None, help='Start date YYYY-MM-DD')
    parser.add_argument('--dry-run', action='store_true', help='Print first 10 rows, no write')
    args = parser.parse_args()

    start = datetime.date.fromisoformat(args.start) if args.start else START_DATE
    today = datetime.date.today()

    print('Loading data series...')
    series = load_data()

    # Load existing scores.json for entries BEFORE start (preserve history)
    existing: list[dict] = []
    if os.path.exists(OUT_PATH):
        try:
            existing = json.load(open(OUT_PATH, encoding='utf-8'))
        except Exception:
            pass

    # Build scores_history from existing entries before start date
    # Format: (date_str, final_score, phase, w_bot)
    scores_history: list[tuple] = [
        (e['date'], e.get('final_score'), e.get('phase'), e.get('w_bot'))
        for e in existing
        if e.get('date') and e['date'] < start.isoformat()
    ]

    # Keep entries before start unchanged; we'll replace from start onward
    kept = [e for e in existing if e.get('date') and e['date'] < start.isoformat()]
    new_entries: list[dict] = []

    date = start
    printed = 0
    while date <= today:
        date_str = date.isoformat()

        raw, btc_price = build_raw(date, series)
        if raw is None:
            date += datetime.timedelta(days=1)
            continue

        prev = _prev_scores(date, series)

        try:
            out = compute_score(
                raw_metrics    = raw,
                target_date    = date,
                prev_scores    = prev,
                scores_history = scores_history,
                btc_price      = btc_price,
            )
        except Exception as e:
            print(f'  {date_str}: compute_score failed — {e}')
            date += datetime.timedelta(days=1)
            continue

        wr  = out.get('wave_resonance') or {}
        try:
            sig = orchestrate(
                v2_score        = out['final_score'],
                v2_oc_coherence = out.get('oc_coherence', 1.0),
                wr_score        = wr.get('score'),
                wr_coherence    = wr.get('coherence'),
                tiz_maturity    = out.get('tiz_maturity'),
                top_signal      = out.get('top_signal'),
                bot_signal      = out.get('bot_signal'),
                is_v3           = True,
                btc_price       = btc_price,
                target_date     = date,
            )
            final = sig['meta_score']
        except Exception:
            final = out['final_score']

        entry = {
            'date':        date_str,
            'final_score': final,
            'phase':       out['phase'],
            'w_bot':       out['w_bot'],
            'btc_price':   btc_price,
        }
        new_entries.append(entry)

        # Append to in-memory history for next iteration (causal TiZ)
        scores_history.append((date_str, final, out['phase'], out['w_bot']))

        if args.dry_run:
            print(f"  {date_str}  score={final:3d}  phase={out['phase']:7s}  "
                  f"w_bot={out['w_bot']:.2f}  tiz={out['tiz_score']}({out['tiz_days']}d)  "
                  f"wr={wr.get('score')}")
            printed += 1
            if printed >= 10:
                print('  [dry-run: stopping after 10 rows]')
                return

        date += datetime.timedelta(days=1)

    if not args.dry_run:
        merged = kept + new_entries
        # Deduplicate by date (new wins)
        seen = {}
        for e in merged:
            seen[e['date']] = e
        result = sorted(seen.values(), key=lambda x: x['date'])
        write_json(OUT_PATH, result)
        print(f'\nWrote {len(result)} entries to {OUT_PATH}')
        print(f'Range: {result[0]["date"]} → {result[-1]["date"]}')

        # Spot-check key dates
        check = {e['date']: e for e in result}
        for d in ['2018-12-15', '2022-06-18', '2022-11-21', '2021-11-10']:
            if d in check:
                e = check[d]
                print(f'  {d}: score={e["final_score"]:3d}  phase={e.get("phase","?")}  w_bot={e.get("w_bot","?")}')


if __name__ == '__main__':
    main()
