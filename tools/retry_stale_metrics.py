#!/usr/bin/env python3
"""Retry fetching metrics that haven't been updated for a given threshold.

Usage:
    python tools/retry_stale_metrics.py [--threshold-hours N] [--metrics m1,m2]

Options:
    --threshold-hours N   Consider a metric stale if its 'updated' timestamp is
                          older than N hours (default: 25).
    --metrics m1,m2,...   Comma-separated list of metric names to check/retry.
                          Defaults to all retryable metrics.
    --dry-run             Print which metrics are stale without re-fetching.

Exit codes:
    0  All metrics refreshed (or none were stale).
    1  One or more metrics still None after retry.
"""
import argparse
import datetime
import json
import os
import sys
import time
import random
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Path to the live data file
DATA_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'data.json')

# --------------------------------------------------------------------------- #
# Mapping: metric key → (fetch_fn, extractor)
# Populated lazily to avoid circular imports at module level.
# --------------------------------------------------------------------------- #

def _build_metric_specs():
    from scraper import nupl as nupl_mod
    from scraper import mvrv as mvrv_mod
    from scraper import sopr as sopr_mod
    from scraper import addresses_in_loss as addr_mod
    from scraper import m2_metric as m2_mod
    from scraper import dxy_metric as dxy_mod
    from scraper import geopolitical_risk as gr_mod
    from scraper import cvdd as cvdd_mod
    from scraper import rhodl as rhodl_mod
    from scraper import rainbow as rainbow_mod
    from scraper.alternative_me import get_fear_greed
    from scraper.coingecko import get_price
    from scraper.cipherb import get_cipherb
    from scraper.smc import get_smc
    from scraper.funding_rate import get_funding_rate

    return {
        'nupl': (nupl_mod.get_nupl, lambda r: r),
        'mvrv': (mvrv_mod.get_mvrv, lambda r: r),
        'sopr': (sopr_mod.get_sopr, lambda r: r),
        'addresses_in_loss': (addr_mod.get_addresses_in_loss, lambda r: r),
        'm2': (m2_mod.get_m2, lambda r: r),
        'dxy': (
            dxy_mod.get_dxy_change,
            lambda r: (r[0] if isinstance(r, (list, tuple)) and len(r) > 0
                       else (r.get('current') if isinstance(r, dict) else r)),
        ),
        'geopolitical_risk': (
            gr_mod.get_geopolitical_risk_change,
            lambda r: (r[0] if isinstance(r, (list, tuple)) and len(r) > 0
                       else (r.get('current') if isinstance(r, dict) else r)),
        ),
        'cvdd_ratio': (cvdd_mod.get_cvdd_ratio, lambda r: r),
        'rhodl_ratio': (rhodl_mod.get_rhodl_ratio, lambda r: r),
        'rainbow_band': (rainbow_mod.get_rainbow_band, lambda r: r),
        'fear_greed': (get_fear_greed, lambda r: r.get('value') if isinstance(r, dict) else r),
        'btc_price': (get_price, lambda r: r.get('price') if isinstance(r, dict) else r),
        'cipherb': (lambda: get_cipherb('BTCUSDT'), lambda r: r.get('last') if isinstance(r, dict) else r),
        'smc': (lambda: get_smc('BTCUSDT', timeframe='1w', size=10), lambda r: r.get('last') if isinstance(r, dict) else r),
        'funding_rate': (lambda: get_funding_rate('BTCUSDT'), lambda r: r),
    }


def _now_iso():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'


def _metric_age_hours(metric_entry: dict, data_ts: str) -> float:
    """Return metric age in hours, using metric's own 'updated' field or the
    top-level data.json timestamp as fallback."""
    ts = metric_entry.get('updated') if isinstance(metric_entry, dict) else None
    if not ts:
        ts = data_ts
    if not ts:
        return float('inf')
    try:
        dt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return float('inf')


def load_data() -> dict:
    path = os.path.realpath(DATA_PATH)
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def save_data(data: dict):
    path = os.path.realpath(DATA_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def find_stale_metrics(data: dict, threshold_hours: float, candidates: list[str]) -> list[str]:
    """Return metric names that are either missing or older than threshold_hours."""
    metrics = data.get('metrics', {})
    data_ts = data.get('timestamp')
    stale = []
    for name in candidates:
        entry = metrics.get(name)
        if entry is None:
            logger.info('%-22s → missing entirely', name)
            stale.append(name)
            continue
        age = _metric_age_hours(entry, data_ts)
        if age > threshold_hours:
            logger.info('%-22s → stale (%.1f h old)', name, age)
            stale.append(name)
        else:
            logger.debug('%-22s → OK (%.1f h old)', name, age)
    return stale


def retry_metrics(stale: list[str], data: dict, specs: dict) -> dict[str, bool]:
    """Fetch each stale metric and update `data` in-place. Returns {name: success}."""
    from scraper.utils import is_valid_metric

    results = {}
    for name in stale:
        if name not in specs:
            logger.warning('No fetch spec for metric "%s", skipping', name)
            results[name] = False
            continue

        fetch_fn, extractor = specs[name]
        logger.info('Fetching %s …', name)
        try:
            raw = fetch_fn()
        except Exception as exc:
            logger.warning('  %s fetch raised: %s', name, exc)
            raw = None

        try:
            val = extractor(raw)
        except Exception:
            val = raw

        # Validate
        if not is_valid_metric(name, val):
            # addresses_in_profit is a special derived field, skip validation
            if name not in ('rainbow_band', 'fear_greed', 'btc_price', 'cipherb', 'smc', 'funding_rate'):
                logger.warning('  %s value failed validation: %r', name, val)
                val = None

        success = val is not None
        results[name] = success
        status = 'OK' if success else 'FAILED'
        logger.info('  %s → %s (%r)', name, status, val)

        # Patch data in memory
        if 'metrics' not in data:
            data['metrics'] = {}

        now = _now_iso()

        if name == 'addresses_in_loss' and val is not None:
            data['addresses_in_loss'] = val
            data['addresses_in_profit'] = max(0.0, 100.0 - float(val))
            data['metrics']['addresses_in_loss'] = {'value': val, 'source': 'BMP', 'updated': now}
            data['metrics']['addresses_in_profit'] = {'value': data['addresses_in_profit'], 'source': 'Derived', 'updated': now}

        elif name == 'nupl' and val is not None:
            data['nupl'] = val
            data['metrics']['nupl'] = {'value': val, 'source': 'MacroMicro', 'updated': now}

        elif name == 'mvrv' and val is not None:
            data['mvrv_z_score'] = val
            data['metrics']['mvrv'] = {'value': val, 'source': 'MacroMicro', 'updated': now}

        elif name == 'sopr' and val is not None:
            data['sopr'] = val
            data['metrics']['sopr'] = {'value': val, 'source': 'MacroMicro', 'updated': now}

        elif name == 'm2' and val is not None:
            data['m2'] = val
            data['metrics']['m2'] = {'value': val, 'source': 'BMP', 'updated': now}

        elif name == 'dxy' and val is not None:
            data['metrics']['dxy'] = {'value': val, 'source': 'MacroMicro', 'updated': now}

        elif name == 'geopolitical_risk' and val is not None:
            data['metrics']['geopolitical_risk'] = {'value': val, 'source': 'MacroMicro', 'updated': now}

        elif name == 'cvdd_ratio' and val is not None:
            data['cvdd_ratio'] = val
            data['metrics']['cvdd_ratio'] = {'value': val, 'source': 'BMP', 'updated': now}

        elif name == 'rhodl_ratio' and val is not None:
            data['rhodl_ratio'] = val
            data['metrics']['rhodl_ratio'] = {'value': val, 'source': 'BMP', 'updated': now}

        elif name == 'rainbow_band' and val is not None:
            data['rainbow_band'] = val
            data['metrics']['rainbow_band'] = {'value': val, 'source': 'BMP', 'updated': now}

        elif name == 'fear_greed' and val is not None:
            fg_raw = raw if isinstance(raw, dict) else {}
            data['fear_greed'] = val
            data['fear_greed_label'] = fg_raw.get('label')
            data['metrics']['fear_greed'] = {'value': val, 'label': fg_raw.get('label'), 'source': 'Alternative.me', 'updated': now}

        elif name == 'btc_price' and val is not None:
            data['btc_price'] = val

        elif name == 'cipherb' and val is not None:
            data['metrics']['cipherb'] = {'value': val, 'source': 'Local', 'updated': now}

        elif name == 'smc' and val is not None:
            data['metrics']['smc'] = {'value': val, 'source': 'Local', 'updated': now}

        elif name == 'funding_rate' and raw is not None:
            source = raw.get('source', 'Bybit') if isinstance(raw, dict) else 'Bybit'
            data['metrics']['funding_rate'] = {'value': raw, 'source': source, 'updated': now}

        # Random pause between requests to avoid hammering sources
        if name != stale[-1]:
            time.sleep(1 + random.random() * 2)

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--threshold-hours', type=float, default=25.0,
                        help='Hours after which a metric is considered stale (default: 25)')
    parser.add_argument('--metrics', type=str, default=None,
                        help='Comma-separated metric names to check (default: all)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Only report stale metrics, do not re-fetch')
    args = parser.parse_args()

    # Default set of retryable metrics (excludes derived ones computed from others)
    default_metrics = [
        'btc_price', 'fear_greed',
        'nupl', 'mvrv', 'sopr', 'addresses_in_loss',
        'm2', 'dxy', 'geopolitical_risk',
        'cvdd_ratio', 'rhodl_ratio', 'rainbow_band',
        'cipherb', 'smc', 'funding_rate',
    ]

    candidates = [m.strip() for m in args.metrics.split(',')] if args.metrics else default_metrics

    data = load_data()
    if not data:
        logger.error('data/data.json not found or empty — run the main scraper first.')
        sys.exit(1)

    stale = find_stale_metrics(data, args.threshold_hours, candidates)

    if not stale:
        logger.info('All checked metrics are fresh. Nothing to retry.')
        sys.exit(0)

    logger.info('\n%d stale metric(s): %s', len(stale), ', '.join(stale))

    if args.dry_run:
        logger.info('--dry-run: skipping fetch.')
        sys.exit(0)

    specs = _build_metric_specs()
    results = retry_metrics(stale, data, specs)

    save_data(data)
    logger.info('Saved updated data/data.json')

    failed = [name for name, ok in results.items() if not ok]
    if failed:
        logger.warning('Could not refresh: %s', ', '.join(failed))
        sys.exit(1)

    logger.info('All stale metrics refreshed successfully.')
    sys.exit(0)


if __name__ == '__main__':
    # Ensure imports work from the project root
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    main()
