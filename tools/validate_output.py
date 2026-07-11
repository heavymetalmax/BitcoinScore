#!/usr/bin/env python3
"""
Pipeline output validator for BitcoinScore.

Run directly:  python tools/validate_output.py
Imported by:   scraper/scraper.py at end of main()

Exits with code 1 and a clear error on any validation failure.
Prints a one-line summary on success.
"""

import json
import os
import sys


# ── Expected metric keys (at least one alias per metric must be present) ─────
REQUIRED_METRICS = [
    # Each inner list: at least one must be present in data['metrics']
    ['nupl'],
    ['mvrv', 'mvrv_z_score'],
    ['rhodl', 'rhodl_ratio'],
    ['cvdd', 'cvdd_ratio'],
    ['asopr'],
    ['cipherb'],
    ['mayer_multiple', 'mayer'],
    ['etf_flows'],
    ['fear_greed'],
    ['yield_curve', 'yield_curve_spread'],
    ['m2', 'm2_yoy', 'm2_mom'],
    ['dxy'],
]

VALID_PHASES = {'BOTTOM', 'NEUTRAL', 'TOP'}

DATA_JSON   = 'data/data.json'
WEB_JSON    = 'web/data.json'
SPARKLINES  = 'web/sparklines.json'


def _load(path):
    if not os.path.exists(path):
        print(f'ERROR: file not found: {path}')
        sys.exit(1)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def validate():
    errors = []

    # ── 1. Load data/data.json ─────────────────────────────────────────────────
    data = _load(DATA_JSON)

    # ── 2. final_score (displayed on site) + v3_score (raw V3 pre-orchestration)
    final_score = data.get('final_score')
    v3_score    = data.get('v3_score')
    display_score = final_score  # what the website shows via data.final_score

    if final_score is None:
        errors.append('final_score is missing')
    elif not isinstance(final_score, (int, float)) or not (0 <= final_score <= 100):
        errors.append(f'final_score out of range 0–100: {final_score}')

    if v3_score is None:
        errors.append('v3_score is missing')
    elif not isinstance(v3_score, (int, float)) or not (0 <= v3_score <= 100):
        errors.append(f'v3_score out of range 0–100: {v3_score}')

    # ── 3. v3_phase ────────────────────────────────────────────────────────────
    v3_phase = data.get('v3_phase')
    if v3_phase not in VALID_PHASES:
        errors.append(f'v3_phase invalid (got {v3_phase!r}, expected one of {VALID_PHASES})')

    # ── 4. btc_price ──────────────────────────────────────────────────────────
    btc_price = data.get('btc_price')
    if not isinstance(btc_price, (int, float)) or btc_price <= 0:
        errors.append(f'btc_price must be > 0, got {btc_price!r}')

    # ── 5. Required metrics ────────────────────────────────────────────────────
    metrics = data.get('metrics', {})
    for aliases in REQUIRED_METRICS:
        found = any(
            k in metrics and metrics[k] is not None
            for k in aliases
        )
        if not found:
            errors.append(f'Missing metric (need one of {aliases})')

    # ── 6. web/data.json sync ─────────────────────────────────────────────────
    web = _load(WEB_JSON)
    web_score       = web.get('final_score')
    web_score_v3    = web.get('v3_score')
    web_ts          = web.get('timestamp')
    data_ts         = data.get('timestamp')

    if web_score != final_score:
        errors.append(
            f'web/data.json final_score ({web_score}) does not match '
            f'data/data.json final_score ({final_score}) — stale web file'
        )
    if web_ts != data_ts:
        errors.append(
            f'web/data.json timestamp ({web_ts}) does not match '
            f'data/data.json timestamp ({data_ts}) — stale web file'
        )

    # ── 7. web/sparklines.json quality ────────────────────────────────────────
    if not os.path.exists(SPARKLINES):
        errors.append(f'{SPARKLINES} does not exist')
    else:
        sp = _load(SPARKLINES)
        # Exclude 'dates' key — it's an index, not a metric series.
        # Also exclude low-frequency metrics (weekly/monthly sources) that are
        # legitimately sparse in any 365-day window.
        LOW_FREQ_METRICS = {'yield_curve_spread', 'm2_yoy'}
        metric_keys = [k for k in sp if k != 'dates' and k not in LOW_FREQ_METRICS]
        if not metric_keys:
            errors.append('sparklines.json has no metric keys')
        for k in metric_keys:
            vals = sp[k]
            if not isinstance(vals, list):
                continue
            n = len(vals)
            null_count = sum(1 for v in vals if v is None)
            if n > 0 and null_count / n > 0.90:
                errors.append(
                    f'sparklines.json[{k!r}]: {null_count}/{n} values are null '
                    f'(>{90}%)'
                )

    # ── Report ────────────────────────────────────────────────────────────────
    if errors:
        for e in errors:
            print(f'VALIDATION ERROR: {e}')
        sys.exit(1)

    price_fmt = f'${btc_price:,.0f}' if isinstance(btc_price, (int, float)) else str(btc_price)
    print(f'Pipeline OK: final_score={final_score} (v3_raw={v3_score}), phase={v3_phase}, btc_price={price_fmt}')


if __name__ == '__main__':
    validate()
