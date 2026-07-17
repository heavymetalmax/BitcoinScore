#!/usr/bin/env python3
"""
Data quality validator for data/history/scores.json.

Checks (by severity):
  ERROR   — data is definitely wrong
  WARNING — suspicious, needs manual review
  INFO    — minor anomaly worth logging

Run:
    python3 tools/validate_data.py              # from 2017-01-01 (modern era default)
    python3 tools/validate_data.py --all        # full history including noisy early era
    python3 tools/validate_data.py --from 2020-01-01
    python3 tools/validate_data.py --verbose
"""
import argparse
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modern era (2014+): liquid market, reliable on-chain data, daily sources
# Early era (pre-2014): <10 tx/day, CVDD/NUPL swing violently, mayer data sparse
EARLY_ERA_CUTOFF = '2014-01-01'

# Hard physical limits (values that are simply impossible regardless of era)
HARD_LIMITS = {
    'nupl':           (-300.0, 300.0),
    'mvrv':           (-1.0, 25.0),
    'rhodl_ratio':    (0.0, 1e8),
    'cvdd_ratio':     (0.1, 300.0),
    'asopr':          (0.5, 3.0),
    'mayer_multiple': (0.05, 15.0),
    'fear_greed':     (0.0, 100.0),
    'cipherb_daily':  (0.0, 100.0),
    'pi_gap_pct':     (-10.0, 85.0),
    'funding_rate':   (-1.0, 1.0),
    'final_score':    (0.0, 100.0),
}

# Max single-day change — two tiers by era.
# Early era is excluded from this check entirely (too sparse/volatile).
MAX_DAILY_DELTA_MODERN = {
    'nupl':           50.0,
    'mvrv':           3.5,
    'cvdd_ratio':     8.0,
    'asopr':          0.25,
    'mayer_multiple': 0.60,   # large weekly candle can move it a lot
    'fear_greed':     55.0,
    'cipherb_daily':  65.0,
    'final_score':    40.0,
}

# Stale data: max consecutive days with identical value before flagging.
# If price moved ≥ price_pct% during that streak → WARNING (suspicious freeze)
# If price was flat → INFO (metric just moves slowly)
MAX_STALE_DAYS = {
    'nupl':           5,
    'mvrv':           5,
    'rhodl_ratio':    21,   # slow-moving by design
    'cvdd_ratio':     14,
    'asopr':          7,
    'mayer_multiple': 5,
    'fear_greed':     3,
    'cipherb_daily':  10,
    'pi_gap_pct':     7,
    'final_score':    7,
}
STALE_PRICE_MOVED_PCT = 8.0   # price must move this much to upgrade INFO → WARNING

# Metrics that should move in the same direction as BTC price over a window.
# window: rolling days, price_pct: minimum price move to trigger check.
PRICE_CORRELATED = {
    'mvrv':           {'window': 30, 'price_pct': 20},
    'mayer_multiple': {'window': 14, 'price_pct': 10},
    'nupl':           {'window': 60, 'price_pct': 25},
    'fear_greed':     {'window':  7, 'price_pct': 12},
    'final_score':    {'window': 30, 'price_pct': 30},
}
# Max tolerated violation rate before reporting as WARN/BAD in correlation check
CORR_WARN_PCT = 12.0
CORR_BAD_PCT  = 25.0

# Cross-metric sanity rules: (metric, op, threshold) → (score, op, threshold, description)
# Only applied in modern era where normalization is stable.
SCORE_SANITY = [
    ('mayer_multiple', '>', 2.2,  'final_score', '>', 60,
     'mayer>2.2 → score should be elevated (deep bull)'),
    ('mayer_multiple', '<', 0.65, 'final_score', '<', 40,
     'mayer<0.65 → score should be low (deep bear)'),
    ('mvrv',           '>', 3.5,  'final_score', '>', 58,
     'mvrv>3.5 → score should be elevated'),
    ('mvrv',           '<', 0.4,  'final_score', '<', 40,
     'mvrv<0.4 → score should be low (unrealized losses)'),
]

# mvrv-nupl divergence: use rolling percentile rank instead of global range
# to avoid early-era extremes skewing the normalization.
MVRV_NUPL_WINDOW   = 365 * 3   # 3-year rolling window for percentile rank
MVRV_NUPL_DIV_THR  = 0.40      # normalized divergence threshold for WARNING
MVRV_NUPL_INFO_THR = 0.30      # INFO threshold


def load():
    path = os.path.join(ROOT, 'data', 'history', 'scores.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def pct_change(a, b):
    if a is None or b is None or a == 0:
        return None
    return (b - a) / abs(a) * 100


def rolling_percentile_rank(values, current):
    """Rank of current within values list → [0, 1]."""
    if not values:
        return None
    return sum(1 for v in values if v <= current) / len(values)


class Report:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.counts  = defaultdict(int)
        self.issues  = []

    def add(self, severity, check, date, msg):
        self.counts[severity] += 1
        self.issues.append((severity, check, date, msg))
        if self.verbose or severity == 'ERROR':
            print(f'  [{severity}] {date}  {check}: {msg}')

    def summary(self):
        print(f'\n{"="*60}')
        print(f'SUMMARY: {self.counts["ERROR"]} errors, '
              f'{self.counts["WARNING"]} warnings, '
              f'{self.counts["INFO"]} info')
        errors_warnings = [(s, c, d, m) for s, c, d, m in self.issues
                           if s in ('ERROR', 'WARNING')]
        if not self.verbose and errors_warnings:
            print(f'\nErrors and warnings ({len(errors_warnings)}):')
            for sev, check, date, msg in errors_warnings:
                print(f'  [{sev}] {date}  {check}: {msg}')


# ── Checks ────────────────────────────────────────────────────────────────────

def check_hard_limits(rows, rep):
    print('Checking hard limits...')
    n = 0
    for row in rows:
        d = row.get('date', '?')
        for field, (lo, hi) in HARD_LIMITS.items():
            v = row.get(field)
            if v is None:
                continue
            if v < lo or v > hi:
                n += 1
                rep.add('ERROR', 'hard_limit', d,
                        f'{field}={v:.4f} outside [{lo}, {hi}]')
    print(f'  Hard limit violations: {n}')


def check_stale_data(rows, rep):
    """Flag metric freezes — especially suspicious when price moved."""
    print('Checking stale data (frozen metric while price moved)...')
    streak   = {}   # field → (value, streak_start_date, count)
    price_at = {}   # date → btc_price (for lookback)

    for row in rows:
        d     = row.get('date', '?')
        price = row.get('btc_price')
        if price is not None:
            price_at[d] = price

        for field, max_days in MAX_STALE_DAYS.items():
            v = row.get(field)
            if v is None:
                streak.pop(field, None)
                continue

            if field in streak:
                sv, sdate, scount = streak[field]
                if v == sv:
                    new_count = scount + 1
                    streak[field] = (sv, sdate, new_count)
                    # Emit exactly once when threshold crossed
                    if new_count == max_days + 1:
                        p_start = price_at.get(sdate)
                        p_now   = price
                        chg     = pct_change(p_start, p_now)
                        if chg is not None and abs(chg) >= STALE_PRICE_MOVED_PCT:
                            rep.add('WARNING', 'stale_frozen', d,
                                    f'{field}={sv} frozen {new_count}d '
                                    f'while price moved {chg:+.1f}% '
                                    f'({sdate}→{d})')
                        else:
                            rep.add('INFO', 'stale_data', d,
                                    f'{field}={sv} unchanged {new_count}d '
                                    f'({sdate}→{d})')
                else:
                    streak[field] = (v, d, 1)
            else:
                streak[field] = (v, d, 1)


MAX_GAP_FOR_JUMP_CHECK = 3  # days — skip jump check if previous value is older

def check_daily_jumps(rows, rep):
    """Flag single-day changes that exceed physical limits (modern era only).

    Only compares to the previous value if it was recorded within
    MAX_GAP_FOR_JUMP_CHECK days — avoids false positives from sparse historical data.
    """
    print(f'Checking impossible single-day jumps (from {EARLY_ERA_CUTOFF})...')
    import datetime
    prev_vals = {}   # field → (value, date_str)
    n = 0
    for row in rows:
        d = row.get('date', '?')
        is_modern = d >= EARLY_ERA_CUTOFF
        for field, max_delta in MAX_DAILY_DELTA_MODERN.items():
            v = row.get(field)
            prev = prev_vals.get(field)
            if v is not None and prev is not None and is_modern:
                pv, pd = prev
                try:
                    gap = (datetime.date.fromisoformat(d)
                           - datetime.date.fromisoformat(pd)).days
                except ValueError:
                    gap = 999
                if gap <= MAX_GAP_FOR_JUMP_CHECK:
                    delta = abs(v - pv)
                    if delta > max_delta:
                        n += 1
                        rep.add('ERROR', 'impossible_jump', d,
                                f'{field}: {pv:.3f}→{v:.3f} (Δ={delta:.3f}, '
                                f'max={max_delta}, gap={gap}d)')
            if v is not None:
                prev_vals[field] = (v, d)
    print(f'  Impossible jump violations: {n}')


def check_price_correlation(rows, rep):
    """Price-metric directional correlation over rolling windows."""
    print('Checking price-metric correlation windows...')
    by_date = {r['date']: r for r in rows if r.get('date')}
    dates   = sorted(by_date.keys())

    for metric, cfg in PRICE_CORRELATED.items():
        win       = cfg['window']
        price_thr = cfg['price_pct']
        violations = 0
        checked    = 0

        for i in range(win, len(dates)):
            d_now  = dates[i]
            d_then = dates[i - win]
            now    = by_date[d_now]
            then   = by_date[d_then]

            if d_then < EARLY_ERA_CUTOFF:
                continue   # early era too volatile to be meaningful

            p_now   = now.get('btc_price')
            p_then  = then.get('btc_price')
            m_now   = now.get(metric)
            m_then  = then.get(metric)

            if None in (p_now, p_then, m_now, m_then):
                continue
            if p_then == 0:
                continue

            price_chg  = pct_change(p_then, p_now)
            metric_chg = m_now - m_then

            if abs(price_chg) < price_thr:
                continue

            checked   += 1
            price_dir  = 1 if price_chg > 0 else -1
            metric_dir = 1 if metric_chg > 0 else (-1 if metric_chg < 0 else 0)

            if metric_dir != 0 and metric_dir != price_dir:
                violations += 1
                rep.add('WARNING', 'price_correlation', d_now,
                        f'{metric}: price {price_chg:+.1f}% over {win}d '
                        f'but metric moved {metric_chg:+.3f} (opposite direction)')

        if checked > 0:
            viol_pct = violations / checked * 100
            status   = ('OK'   if viol_pct < CORR_WARN_PCT else
                        'WARN' if viol_pct < CORR_BAD_PCT  else 'BAD')
            print(f'  {metric:20s}: {checked:4d} windows, '
                  f'{violations:3d} violations ({viol_pct:.1f}%)  [{status}]')
        else:
            print(f'  {metric:20s}: no windows with sufficient price move')


def check_missing_windows(rows, rep):
    """Flag suspiciously long gaps in key metrics."""
    print('Checking missing data windows...')
    thresholds = {'nupl': 45, 'mvrv': 45, 'btc_price': 7, 'final_score': 14}

    gap_start    = {}
    prev_present = {}
    dates_list   = [r.get('date', '') for r in rows]

    for i, row in enumerate(rows):
        d = row.get('date', '?')
        for m, thr in thresholds.items():
            v = row.get(m)
            if v is not None:
                if m in gap_start:
                    gap_rows = i - prev_present.get(m + '_idx', i)
                    if gap_rows > thr:
                        rep.add('WARNING', 'missing_window', d,
                                f'{m} absent {gap_rows} rows '
                                f'({gap_start[m]}→{d})')
                    del gap_start[m]
                prev_present[m + '_idx'] = i
                prev_present[m]          = d
            else:
                if m not in gap_start and m in prev_present:
                    gap_start[m] = d


def check_score_sanity(rows, rep):
    """Cross-metric consistency checks (modern era only)."""
    print(f'Checking score-vs-metric sanity (from {EARLY_ERA_CUTOFF})...')
    violations = 0
    for row in rows:
        d = row.get('date', '?')
        if d < EARLY_ERA_CUTOFF:
            continue
        for (m1, op1, thr1, m2, op2, thr2, desc) in SCORE_SANITY:
            v1 = row.get(m1)
            v2 = row.get(m2)
            if v1 is None or v2 is None:
                continue
            cond1 = (v1 > thr1 if op1 == '>' else v1 < thr1)
            cond2 = (v2 > thr2 if op2 == '>' else v2 < thr2)
            if cond1 and not cond2:
                violations += 1
                rep.add('WARNING', 'score_sanity', d,
                        f'{desc}: {m1}={v1:.2f}, {m2}={v2:.1f}')
    print(f'  Score sanity violations: {violations}')


def check_mvrv_nupl_divergence(rows, rep):
    """mvrv and nupl should trend together — use rolling percentile rank to compare."""
    print(f'Checking mvrv-nupl divergence (rolling {MVRV_NUPL_WINDOW}d window)...')
    modern = [r for r in rows if r.get('date', '') >= EARLY_ERA_CUTOFF]
    dates  = [r['date'] for r in modern]

    # pre-build sorted mvrv and nupl lists per date position
    mvrv_hist = []
    nupl_hist = []

    warnings = 0
    infos    = 0

    for i, row in enumerate(modern):
        d    = row.get('date', '?')
        mvrv = row.get('mvrv')
        nupl = row.get('nupl')

        # advance window
        cutoff = dates[i] if i < len(dates) else '9999'
        # instead of rebuilding, accumulate and use a sliding window
        while len(mvrv_hist) > MVRV_NUPL_WINDOW:
            mvrv_hist.pop(0)
        while len(nupl_hist) > MVRV_NUPL_WINDOW:
            nupl_hist.pop(0)

        if mvrv is not None:
            mvrv_hist.append(mvrv)
        if nupl is not None:
            nupl_hist.append(nupl)

        if mvrv is None or nupl is None:
            continue
        if len(mvrv_hist) < 30 or len(nupl_hist) < 30:
            continue

        mvrv_rank = rolling_percentile_rank(mvrv_hist, mvrv)
        nupl_rank = rolling_percentile_rank(nupl_hist, nupl)
        div       = abs(mvrv_rank - nupl_rank)

        if div >= MVRV_NUPL_DIV_THR:
            warnings += 1
            rep.add('WARNING', 'mvrv_nupl_divergence', d,
                    f'mvrv rank={mvrv_rank:.2f} vs nupl rank={nupl_rank:.2f} '
                    f'(div={div:.2f}, mvrv={mvrv:.3f}, nupl={nupl:.1f}%)')
        elif div >= MVRV_NUPL_INFO_THR:
            infos += 1
            rep.add('INFO', 'mvrv_nupl_divergence', d,
                    f'mvrv rank={mvrv_rank:.2f} vs nupl rank={nupl_rank:.2f} '
                    f'(div={div:.2f})')

    print(f'  mvrv-nupl divergence: {warnings} warnings, {infos} info')


def check_scores_completeness(rows, rep):
    """Every row with on-chain metrics should have a final_score."""
    print('Checking score completeness...')
    missing = 0
    for row in rows:
        has_metrics = any(row.get(k) is not None
                          for k in ('nupl', 'mvrv', 'rhodl_ratio'))
        if has_metrics and row.get('final_score') is None:
            missing += 1
            rep.add('WARNING', 'missing_score', row.get('date', '?'),
                    'has on-chain metrics but no final_score')
    print(f'  Rows missing final_score: {missing}')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Validate scores.json data quality')
    parser.add_argument('--from', dest='from_date', default='2017-01-01',
                        help='start date YYYY-MM-DD (default: 2017-01-01)')
    parser.add_argument('--all', dest='full_history', action='store_true',
                        help='validate full history including noisy early era')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    all_rows = load()

    if args.full_history:
        rows     = all_rows
        from_str = 'full history'
    else:
        rows     = [r for r in all_rows if r.get('date', '') >= args.from_date]
        from_str = f'from {args.from_date}'

    print(f'Validating {len(rows)} rows ({from_str}, total in db: {len(all_rows)})')
    print()

    rep = Report(verbose=args.verbose)

    check_hard_limits(rows, rep)
    check_stale_data(rows, rep)
    check_daily_jumps(rows, rep)
    check_price_correlation(rows, rep)
    check_missing_windows(rows, rep)
    check_score_sanity(rows, rep)
    check_mvrv_nupl_divergence(rows, rep)
    check_scores_completeness(rows, rep)

    rep.summary()

    return 1 if rep.counts['ERROR'] > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
