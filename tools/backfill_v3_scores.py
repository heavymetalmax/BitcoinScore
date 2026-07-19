#!/usr/bin/env python3
"""Recompute final_score/phase/w_bot/w_top in data/history/scores.json using V4.

Reads raw metrics DIRECTLY from each scores.json entry (nupl, mvrv, etc.)
and supplements with separate history files (yield_curve, lth_supply,
cipherb_weekly, etf_flows).  Updates entries IN-PLACE — raw metrics are
preserved untouched.  Does NOT call orchestrate(): pure V4 compute_score().

TiZ is built causally in memory so it never reads/writes a circular source.

Usage:
    python tools/backfill_v4_scores.py
    python tools/backfill_v4_scores.py --from 2018-01-01
    python tools/backfill_v4_scores.py --dry-run   # print 10 rows, no write
"""
import sys, os, json, datetime, argparse
sys.path.insert(0, '.')

from tools.backtest import load_data, nearest_strict
from scraper.score import compute_score
from scraper.utils import write_json

OUT_PATH   = 'data/history/scores.json'
START_DATE = datetime.date(2018, 1, 1)


def _make_raw(entry: dict, td: datetime.date, series: dict) -> dict:
    """Build raw_metrics dict for compute_score() from a scores.json entry."""
    wk,    _ = nearest_strict(series.get('cipherb_weekly',    []), td)
    wk_fb, _ = nearest_strict(series.get('cipherb_weekly_fb', []), td)
    cb_daily  = entry.get('cipherb_daily')
    if wk is not None:
        cb_dict = {'weekly_score': wk, 'daily_score': cb_daily,
                   'fast_bearish_div': bool(wk_fb)}
    elif cb_daily is not None:
        cb_dict = {'weekly_score': cb_daily, 'daily_score': cb_daily,
                   'fast_bearish_div': False}
    else:
        cb_dict = None

    yc,  _ = nearest_strict(series.get('yield_curve_spread', []), td)
    lth, _ = nearest_strict(series.get('lth_supply',         []), td)
    etf, _ = nearest_strict(series.get('etf_flows',          []), td)

    return {
        'nupl':           entry.get('nupl'),
        'mvrv':           entry.get('mvrv'),
        'rhodl_ratio':    entry.get('rhodl_ratio'),
        'cvdd_ratio':     entry.get('cvdd_ratio'),
        'asopr':          entry.get('asopr'),
        'puell_multiple': entry.get('puell'),
        'mayer_multiple': entry.get('mayer_multiple'),
        'fear_greed':     entry.get('fear_greed'),
        'm2_mom':         entry.get('m2_yoy'),
        'yield_curve':    yc,
        'lth_supply_pct': lth,
        'dxy':            entry.get('dxy'),
        'funding_rate':   entry.get('funding_rate'),
        'pi_cycle':       entry.get('pi_gap_pct'),
        'cipherb':        cb_dict,
        'etf_flows':      etf,
    }


def _has_core(raw: dict) -> bool:
    """At least one OC metric must be present to compute a meaningful score."""
    return any(raw.get(k) is not None for k in
               ('nupl', 'mvrv', 'rhodl_ratio', 'cvdd_ratio'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from', dest='start', default=None,
                        help='Start date YYYY-MM-DD (default 2018-01-01)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print first 10 updated rows without writing')
    args = parser.parse_args()

    start = datetime.date.fromisoformat(args.start) if args.start else START_DATE
    today = datetime.date.today()

    print('Loading supplemental series...')
    series = load_data()

    print(f'Loading {OUT_PATH}...')
    all_rows: list[dict] = json.load(open(OUT_PATH, encoding='utf-8'))
    rows_by_date: dict[str, dict] = {r['date']: r for r in all_rows}

    # Build causal TiZ from entries BEFORE start date
    scores_history: list[tuple] = []
    for r in all_rows:
        d = r.get('date', '')
        if d and d < start.isoformat() and r.get('final_score') is not None:
            scores_history.append((d, r['final_score'], r.get('phase'), r.get('w_bot')))

    updated = 0
    skipped = 0
    printed = 0

    date = start
    while date <= today:
        d_str = date.isoformat()
        date += datetime.timedelta(days=1)

        entry = rows_by_date.get(d_str)
        if entry is None:
            skipped += 1
            continue

        raw = _make_raw(entry, datetime.date.fromisoformat(d_str), series)
        if not _has_core(raw):
            skipped += 1
            continue

        try:
            out = compute_score(
                raw_metrics    = raw,
                target_date    = datetime.date.fromisoformat(d_str),
                prev_scores    = None,
                scores_history = scores_history,
                btc_price      = entry.get('btc_price'),
            )
        except Exception as e:
            print(f'  {d_str}: compute_score failed — {e}')
            skipped += 1
            continue

        final = out['final_score']

        # Update in-place — preserve all raw metric fields
        entry['final_score'] = final
        entry['phase']       = out['phase']
        entry['w_bot']       = out['w_bot']
        entry['w_top']       = out['w_top']
        entry['source']      = 'backfill_v4'

        # Advance causal TiZ
        scores_history.append((d_str, final, out['phase'], out['w_bot']))
        updated += 1

        if args.dry_run:
            tiz = out.get('tiz_score')
            print(f'  {d_str}  score={final:3d}  phase={out["phase"]:7s}'
                  f'  w_bot={out["w_bot"]:.2f}  w_top={out["w_top"]:.2f}'
                  f'  tiz={tiz}({out["tiz_days"]}d)')
            printed += 1
            if printed >= 10:
                print('  [dry-run: stopping after 10 rows]')
                return

    print(f'\nDone: {updated} updated, {skipped} skipped (no entry or no core metrics)')

    if not args.dry_run:
        write_json(OUT_PATH, all_rows)
        print(f'Written: {OUT_PATH}')


if __name__ == '__main__':
    main()
