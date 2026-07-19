#!/usr/bin/env python3
"""
Pipeline validator: raw value → normalized score table for one or more dates.

Usage:
    python3 tools/validate_pipeline.py                        # today
    python3 tools/validate_pipeline.py 2025-01-20 2025-10-06 # specific dates
    python3 tools/validate_pipeline.py --all-backtest         # all backtest dates

Output columns:
  METRIC          raw metric name
  RAW             raw value fed into normalizer
  NORM            normalized score 0-100
  DIRECTION       ↑ = higher raw → higher risk (expected), ✗ = reversed/unchecked
  STATUS          OK / WARN / ERR
  NOTE            human-readable flag
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.backtest import load_data, nearest_strict, v3_at

# Expected direction: True = higher raw value means higher risk
# False = lower raw value means higher risk
# None = no simple monotone relationship
EXPECTED_DIRECTION = {
    'nupl':              True,   # higher NUPL = more profit = more top risk
    'mvrv':              True,   # higher MVRV = more overvalued
    'rhodl_ratio':       True,   # higher RHODL = more short-term speculation
    'cvdd_ratio':        True,   # higher = more overvalued vs realized value
    'asopr':             True,   # higher = coins sold at more profit
    'puell_multiple':    True,   # higher miner revenue = more selling pressure
    'mayer_multiple':    True,   # higher = more overextended above 200MA
    'fear_greed':        True,   # higher = more greed = more risk
    'm2_yoy':            False,  # INVERTED: higher M2 growth = more liquidity = tailwind for BTC = lower buy risk
    'yield_curve_spread':False,  # inverted: positive spread = normal = lower risk; negative = recession = higher risk
    'lth_supply':        False,  # higher LTH% = more hodling = less distribution = lower top risk
    'funding_rate':      True,   # higher = more long leverage = more risk
    'dxy':               True,   # higher DXY = risk-off for BTC = more risk
    'cipherb':           True,   # higher = more overbought
    'etf_flows':         True,   # higher inflows = more speculation
}

# Maps metric name in raw_vals to key in normalized_scores dict
NORM_KEY_MAP = {
    'mvrv':           'mvrv_z_score',
    'puell_multiple': 'puell',
}

# Sanity bounds for raw values (warn if outside)
RAW_BOUNDS = {
    'nupl':              (-100, 100),
    'mvrv':              (-2, 20),
    'rhodl_ratio':       (10, 200_000),
    'cvdd_ratio':        (0.1, 30),
    'asopr':             (0.5, 2.0),
    'puell_multiple':    (0.1, 10),
    'mayer_multiple':    (0.3, 5.0),
    'fear_greed':        (0, 100),
    'lth_supply':        (10_000_000, 21_000_000),
    'funding_rate':      (-0.5, 0.5),
    'dxy':               (60, 180),
}

BACKTEST_DATES = [
    ('2018-12-15', '2018 bottom',       3_200),
    ('2021-04-14', '2021 Spring ATH',  63_500),
    ('2021-11-10', '2021 Nov ATH',     69_000),
    ('2022-11-21', 'FTX bottom',       15_500),
    ('2025-01-20', 'Jan 2025',        109_000),
    ('2025-10-06', '2025 ATH',        124_659),
    ('2026-04-25', 'Local low',        77_500),
]


def _fmt_raw(v):
    if v is None:
        return 'None'
    if isinstance(v, float):
        if abs(v) >= 10_000:
            return f'{v:,.0f}'
        if abs(v) >= 100:
            return f'{v:.1f}'
        return f'{v:.4f}'
    return str(v)


def validate_date(td: datetime.date, series: dict, label: str = '') -> None:
    result = v3_at(td, series)
    ns     = result.get('normalized_scores', {})
    ut     = result.get('utilities', {})
    phase  = result.get('phase', '?')
    w_top  = result.get('w_top', 0)
    w_bot  = result.get('w_bot', 0)
    final  = result.get('final_score', '?')

    # Collect raw values from backtest series
    raw_vals = {}
    for key, sname in [
        ('nupl', 'nupl'), ('mvrv', 'mvrv'), ('rhodl_ratio', 'rhodl_ratio'),
        ('cvdd_ratio', 'cvdd_ratio'), ('asopr', 'asopr'),
        ('puell_multiple', 'puell_multiple'), ('mayer_multiple', 'mayer_multiple'),
        ('fear_greed', 'fear_greed'), ('m2_yoy', 'm2_yoy'),
        ('yield_curve_spread', 'yield_curve_spread'),
        ('lth_supply', 'lth_supply'), ('funding_rate', 'funding_rate'),
        ('dxy', 'dxy'), ('cipherb', 'cipherb'),
        ('etf_flows', 'etf_flows'),
    ]:
        v, _ = nearest_strict(series.get(sname, []), td, max_days=30)
        raw_vals[key] = v

    title = f'{td}  {label}  |  phase={phase}  w_top={w_top:.2f}  w_bot={w_bot:.2f}  final={final}'
    print()
    print('=' * 90)
    print(title)
    print('=' * 90)
    print(f'  {"METRIC":<22} {"RAW":>14}  {"NORM":>5}  {"UTIL":>5}  DIR  STATUS  NOTE')
    print('  ' + '-' * 80)

    errors = warnings = 0
    for metric in sorted(EXPECTED_DIRECTION):
        raw     = raw_vals.get(metric)
        norm_k  = NORM_KEY_MAP.get(metric, metric)
        norm    = ns.get(norm_k)
        util    = ut.get(norm_k, ut.get(metric))
        expects_up = EXPECTED_DIRECTION[metric]

        # Direction indicator
        dir_sym = '↑' if expects_up else '↓'

        # Status checks
        status = 'OK'
        note   = ''

        if raw is None:
            status = 'WARN'
            note   = 'raw missing'
            warnings += 1
        elif norm is None:
            status = 'WARN'
            note   = 'norm returned None'
            warnings += 1
        else:
            # Bounds check on raw
            lo, hi = RAW_BOUNDS.get(metric, (None, None))
            if lo is not None and not (lo <= raw <= hi):
                status = 'WARN'
                note   = f'raw {raw:.2f} outside [{lo},{hi}]'
                warnings += 1

            # Norm range check
            if not (0 <= norm <= 100):
                status = 'ERR'
                note   = f'norm={norm} outside [0,100]'
                errors += 1

        raw_str  = _fmt_raw(raw)
        norm_str = f'{norm:3d}' if norm is not None else '---'
        util_str = f'{util:.3f}' if util is not None else '  ---'

        print(f'  {metric:<22} {raw_str:>14}  {norm_str:>5}  {util_str:>5}  {dir_sym}    {status:<6}  {note}')

    print()
    print(f'  → {errors} errors, {warnings} warnings')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('dates', nargs='*', help='YYYY-MM-DD dates to validate')
    parser.add_argument('--all-backtest', action='store_true')
    args = parser.parse_args()

    print('Loading series data...')
    series = load_data()
    print()

    if args.all_backtest:
        for ds, label, _ in BACKTEST_DATES:
            validate_date(datetime.date.fromisoformat(ds), series, label)
    elif args.dates:
        for ds in args.dates:
            validate_date(datetime.date.fromisoformat(ds), series, ds)
    else:
        validate_date(datetime.date.today(), series, 'today')


if __name__ == '__main__':
    main()
