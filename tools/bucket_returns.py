"""Compute forward BTC return statistics by score bucket.

Reads data/history/scores.json, groups daily entries into 10-point buckets
using [low, high) boundaries, computes median/p25/p75 returns and % positive
at 30/90/180/365-day horizons. Writes data/bucket_returns.json.

Boundaries: score=20 lands in [20,30), score=30 in [30,40), score=100 in [90,100].
Symmetric cutoff: entries within the last N days are excluded from the Nd horizon
(no forward data exists yet). Uses bisect for O(log n) horizon lookups.
"""
import bisect
import datetime
import json
import os
import statistics
import sys

SCORES_PATH = 'data/history/scores.json'
OUTPUT_PATH = 'data/bucket_returns.json'

HORIZONS = [30, 90, 180, 365]
BUCKET_SIZE = 10
MIN_N = 8
GAP_DAYS = 3


def _validate(entries):
    if not isinstance(entries, list):
        raise ValueError("scores.json must be a JSON array")
    if not entries:
        raise ValueError("scores.json is empty")
    sample = entries[0]
    if 'date' not in sample or 'final_score' not in sample:
        raise ValueError("entries must have 'date' and 'final_score' keys")


def _pct(val, n_digits=1):
    return round(val, n_digits)


def _percentile(sorted_vals, p):
    """Return p-th percentile (0-100) from a pre-sorted list."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def run():
    if not os.path.exists(SCORES_PATH):
        print(f"[bucket_returns] {SCORES_PATH} not found — aborting", file=sys.stderr)
        return 1

    with open(SCORES_PATH, encoding='utf-8') as f:
        entries = json.load(f)

    _validate(entries)
    print(f"[bucket_returns] Loaded {len(entries)} entries "
          f"({entries[0]['date']} to {entries[-1]['date']})")

    # Pre-build sorted date index for O(log n) lookups
    dates = [datetime.date.fromisoformat(e['date']) for e in entries]
    prices = [e.get('btc_price') for e in entries]
    last_date = dates[-1]

    # Symmetric cutoff per horizon: only entries where date <= last_date - H days
    cutoffs = {h: last_date - datetime.timedelta(days=h) for h in HORIZONS}

    def find_forward_return(base_idx, h_days):
        """Return % price change H days forward from base_idx.
        Returns None if gap to nearest entry > GAP_DAYS or price missing."""
        target_date = dates[base_idx] + datetime.timedelta(days=h_days)
        pos = bisect.bisect_left(dates, target_date)
        best_idx, best_gap = None, GAP_DAYS + 1
        for candidate in (pos - 1, pos):
            if 0 <= candidate < len(dates):
                gap = abs((dates[candidate] - target_date).days)
                if gap < best_gap:
                    best_gap, best_idx = gap, candidate
        if best_idx is None or best_gap > GAP_DAYS:
            return None
        base_price = prices[base_idx]
        target_price = prices[best_idx]
        if not base_price or not target_price:
            return None
        return (target_price - base_price) / base_price * 100.0

    # Group entry indices by bucket
    bucket_indices: dict[int, list[int]] = {}
    for i, e in enumerate(entries):
        score = e.get('final_score')
        if score is None:
            continue
        # [low, high) boundaries; score=100 maps to bucket 90
        low = min(int(score // BUCKET_SIZE) * BUCKET_SIZE, 90)
        bucket_indices.setdefault(low, []).append(i)

    result_buckets = []
    for low in range(0, 100, BUCKET_SIZE):
        high = low + BUCKET_SIZE
        label = f"{low}–{high}"
        indices = bucket_indices.get(low, [])
        n = len(indices)

        if n < MIN_N:
            print(f"[bucket_returns] [{low},{high}): suppressed (n={n} < {MIN_N})")
            result_buckets.append({
                'label': label, 'range_low': low, 'range_high': high,
                'n': n, 'suppressed': True,
                **{f'{stat}_{h}d': None
                   for stat in ('median', 'p25', 'p75', 'pct_positive', 'n')
                   for h in HORIZONS},
            })
            continue

        entry = {'label': label, 'range_low': low, 'range_high': high,
                 'n': n, 'suppressed': False}

        for h in HORIZONS:
            cutoff = cutoffs[h]
            valid_idx = [i for i in indices if dates[i] <= cutoff]
            returns = [r for i in valid_idx
                       for r in [find_forward_return(i, h)] if r is not None]
            n_h = len(returns)
            if n_h == 0:
                entry[f'median_{h}d'] = None
                entry[f'p25_{h}d'] = None
                entry[f'p75_{h}d'] = None
                entry[f'pct_positive_{h}d'] = None
                entry[f'n_{h}d'] = 0
                continue
            sorted_r = sorted(returns)
            entry[f'median_{h}d'] = _pct(statistics.median(sorted_r))
            entry[f'p25_{h}d'] = _pct(_percentile(sorted_r, 25))
            entry[f'p75_{h}d'] = _pct(_percentile(sorted_r, 75))
            entry[f'pct_positive_{h}d'] = round(
                100 * sum(1 for r in returns if r > 0) / n_h
            )
            entry[f'n_{h}d'] = n_h

        result_buckets.append(entry)
        print(
            f"[bucket_returns] [{low},{high}): n={n}  "
            f"median_365d={entry.get('median_365d')}%  "
            f"pct_positive_365d={entry.get('pct_positive_365d')}%  "
            f"(n_365d={entry.get('n_365d')})"
        )

    output = {
        'generated': datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'backtest_type': 'in_sample',
        'total_entries': len(entries),
        'date_range': f"{entries[0]['date']} to {entries[-1]['date']}",
        'buckets': result_buckets,
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"[bucket_returns] Written {len(result_buckets)} buckets to {OUTPUT_PATH}")
    return 0


if __name__ == '__main__':
    sys.exit(run())
