#!/usr/bin/env python3
"""Calibrate DXY Macro Modifier thresholds via historical backtest.

Computes IC(dxy_normalized, fwd_ret_365) and runs a grid search over
(HIGH, LOW, SCALE, MAX) to find constants that maximise IC improvement.

Usage:
    python tools/backtest_dxy.py

Output:
    - IC table by phase
    - Grid search results (top-10 configs)
    - Recommended constants to paste into scoring_pipeline.py
"""
import sys, os, json, math, statistics, datetime, bisect
sys.path.insert(0, '.')

from scraper.scoring import map_dxy

# ── Data loading ──────────────────────────────────────────────────────────────

def _load_dxy_series():
    """Load DXY series → list of (date_str, raw_value)."""
    path = 'data/history/dxy_history.json'
    d = json.load(open(path))
    series = d['series']
    return sorted((r['date'][:10], float(r['value'])) for r in series)


def _load_btc_prices():
    """Load BTC price history → {date_str: close_price}."""
    path = 'data/history/btc_price_history.json'
    d = json.load(open(path))
    series = d.get('series', d)
    prices = {}
    for r in series:
        if isinstance(r, dict):
            dt = str(r.get('date', ''))[:10]
            v  = r.get('close') or r.get('value') or r.get('price')
        elif isinstance(r, list):
            dt, v = str(r[0])[:10], r[1]
        else:
            continue
        if dt and v:
            prices[dt] = float(v)
    return prices


def _load_phase_history():
    """Load historical phase labels from scores.json → {date_str: phase}."""
    path = 'data/history/scores.json'
    if not os.path.exists(path):
        return {}
    records = json.load(open(path))
    return {r['date']: r.get('phase', 'NEUTRAL') for r in records if r.get('date')}


# ── Normalization helpers ──────────────────────────────────────────────────────

_ADAPTIVE_WIN  = 4 * 365  # days rolling window
_ADAPTIVE_BLEND = 0.50    # 50% percentile + 50% fixed map


def _pct_rank_at(series_dates, series_vals, target_iso, value):
    """Rolling percentile of value in data ≤ target_iso."""
    hi_idx = bisect.bisect_right(series_dates, target_iso)
    lo_iso = (
        datetime.date.fromisoformat(target_iso) - datetime.timedelta(days=_ADAPTIVE_WIN)
    ).isoformat()
    lo_idx = bisect.bisect_left(series_dates, lo_iso)
    window = series_vals[lo_idx:hi_idx]
    if len(window) < 24:
        return None
    return sum(1 for v in window if v <= value) / len(window) * 100


def normalize_dxy(series, target_iso):
    """Compute adaptive-blended DXY score (0-100) for target_iso."""
    dates = [d for d, _ in series]
    vals  = [v for _, v in series]
    idx   = bisect.bisect_right(dates, target_iso) - 1
    if idx < 0:
        return None
    raw = vals[idx]
    fixed = map_dxy(raw)
    if fixed is None:
        return None
    pct = _pct_rank_at(dates, vals, target_iso, raw)
    if pct is None:
        return fixed
    return round(_ADAPTIVE_BLEND * pct + (1 - _ADAPTIVE_BLEND) * fixed)


# ── IC helpers ─────────────────────────────────────────────────────────────────

def _pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 30:
        return None, len(pairs)
    xs2, ys2 = zip(*pairs)
    mx, my = statistics.mean(xs2), statistics.mean(ys2)
    sx, sy = statistics.stdev(xs2), statistics.stdev(ys2)
    if sx < 1e-9 or sy < 1e-9:
        return None, len(pairs)
    r = sum((x - mx) * (y - my) for x, y in zip(xs2, ys2)) / (len(xs2) * sx * sy)
    return r, len(pairs)


# ── DXY modifier formula ──────────────────────────────────────────────────────

def dxy_adjustment(dxy_score, high, low, scale, max_adj):
    """Compute DXY modifier given a normalized dxy_score and threshold constants."""
    if dxy_score is None:
        return 0.0
    if dxy_score > high:
        return min(max_adj, (dxy_score - high) * scale)
    if dxy_score < low:
        return -min(max_adj, (low - dxy_score) * scale)
    return 0.0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== DXY MACRO MODIFIER CALIBRATION BACKTEST ===\n")

    dxy_series  = _load_dxy_series()
    btc_prices  = _load_btc_prices()
    phase_hist  = _load_phase_history()

    if not dxy_series:
        print("ERROR: dxy_history.json is empty — run tools/backfill_dxy_history.py first")
        return
    if not btc_prices:
        print("ERROR: btc_price_history.json is empty")
        return

    print(f"DXY series:  {len(dxy_series)} pts  "
          f"({dxy_series[0][0]} → {dxy_series[-1][0]})")
    print(f"BTC prices:  {len(btc_prices)} pts")
    print(f"Phase hist:  {len(phase_hist)} dates")
    print()

    # Build dataset: (date, dxy_score, fwd_ret_365, phase)
    dataset = []
    cutoff  = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()

    all_btc_dates = sorted(btc_prices.keys())

    for date_iso, _ in dxy_series:
        if date_iso < '2018-02-01':     # F&G / data availability
            continue
        if date_iso > cutoff:           # need 365d forward window
            break
        p0 = btc_prices.get(date_iso)
        if p0 is None or p0 == 0:
            continue
        # Forward return: average of prices over the next 365 days
        idx = bisect.bisect_right(all_btc_dates, date_iso)
        window_prices = []
        for off in range(1, 366):
            fd = (datetime.date.fromisoformat(date_iso) + datetime.timedelta(days=off)).isoformat()
            pf = btc_prices.get(fd)
            if pf is not None:
                window_prices.append(pf)
        if not window_prices:
            continue
        fwd_ret_365 = (window_prices[-1] - p0) / p0 * 100

        dxy_score = normalize_dxy(dxy_series, date_iso)
        if dxy_score is None:
            continue

        phase = phase_hist.get(date_iso, 'NEUTRAL')
        dataset.append({
            'date':         date_iso,
            'dxy_score':    dxy_score,
            'fwd_ret_365':  fwd_ret_365,
            'phase':        phase,
        })

    print(f"Dataset: {len(dataset)} date-rows\n")

    # ── Section 1: IC by phase ────────────────────────────────────────────────
    print("=== IC(dxy_normalized, fwd_ret_365) BY PHASE ===")
    print(f"  {'Group':15s}  {'n':>5}  {'IC':>8}  note")
    print("  " + "-" * 48)

    all_dxy   = [r['dxy_score']   for r in dataset]
    all_fwd   = [r['fwd_ret_365'] for r in dataset]
    ic_all, n = _pearson(all_dxy, all_fwd)
    note = ('← ✓ risk signal (add to score when high)' if ic_all and ic_all < -0.05
            else ('← ⚠ momentum (subtract when high?)' if ic_all and ic_all > 0.05 else ''))
    ic_all_str = f'{ic_all:+.3f}' if ic_all is not None else '   N/A'
    print(f"  {'ALL':15s}  {n:>5}  {ic_all_str:>8}  {note}")

    for phase in ('BOTTOM', 'NEUTRAL', 'TOP'):
        rows = [r for r in dataset if r['phase'] == phase]
        ic, n2 = _pearson([r['dxy_score'] for r in rows], [r['fwd_ret_365'] for r in rows])
        ic_str = f'{ic:+.3f}' if ic is not None else '   N/A'
        print(f"  {phase:15s}  {n2:>5}  {ic_str:>8}")

    # ── Section 2: IC by DXY decile ──────────────────────────────────────────
    print("\n=== IC BY DXY SCORE DECILE (all phases) ===")
    print(f"  {'Decile':12s}  {'DXY range':14s}  {'n':>5}  {'IC':>8}")
    print("  " + "-" * 44)
    sorted_dxy = sorted(r['dxy_score'] for r in dataset)
    decile_size = len(sorted_dxy) // 10
    for dec in range(10):
        lo_v = sorted_dxy[dec * decile_size]
        hi_v = sorted_dxy[min((dec + 1) * decile_size - 1, len(sorted_dxy) - 1)]
        rows = [r for r in dataset if lo_v <= r['dxy_score'] <= hi_v]
        ic, n2 = _pearson([r['dxy_score'] for r in rows], [r['fwd_ret_365'] for r in rows])
        ic_str = f'{ic:+.3f}' if ic is not None else '   N/A'
        print(f"  {dec+1:>2}  [{lo_v:>2.0f}-{hi_v:>2.0f}]       {n2:>5}  {ic_str:>8}")

    # ── Section 3: Grid search ────────────────────────────────────────────────
    print("\n=== GRID SEARCH: (HIGH, LOW, SCALE, MAX) ===")
    print("Metric: IC improvement vs no-modifier baseline on a proxy score.\n")

    # Proxy base score: use 50 (neutral) as baseline — modifier is additive delta
    # Evaluate by IC(50 + adj, fwd_ret_365). Since IC is linear-invariant to
    # additive constants, this is equivalent to IC(adj, fwd_ret_365 - mean_fwd_ret).
    # Alternatively, measure whether high-DXY zone rows have lower median fwd returns.

    # For simplicity: compute correlation between modifier output and fwd_ret
    # (positive adj when risk is high → fwd_ret should be negative → IC of adj ≈ negative)
    # We WANT IC(adj, fwd_ret) < 0, i.e. high adjustment correctly predicts lower returns.

    highs  = [55, 60, 65, 70, 75]
    lows   = [25, 30, 35, 40, 45]
    scales = [0.10, 0.15, 0.20, 0.25, 0.30]
    maxs   = [3.0, 4.0, 5.0]

    results = []
    for high in highs:
        for low in lows:
            if low >= high - 10:
                continue
            for scale in scales:
                for max_adj in maxs:
                    adjs = [dxy_adjustment(r['dxy_score'], high, low, scale, max_adj)
                            for r in dataset]
                    ic_adj, n3 = _pearson(adjs, all_fwd)
                    if ic_adj is None:
                        continue
                    # We want IC < 0 (adj is positive when risk is high → lower fwd returns)
                    results.append({
                        'high': high, 'low': low, 'scale': scale, 'max': max_adj,
                        'ic': ic_adj, 'n': n3,
                    })

    # Sort: most negative IC first (strongest correct signal)
    results.sort(key=lambda r: r['ic'])

    print(f"  {'HIGH':>5}  {'LOW':>5}  {'SCALE':>6}  {'MAX':>5}  {'IC':>8}  {'n':>5}")
    print("  " + "-" * 52)
    for r in results[:15]:
        print(f"  {r['high']:>5}  {r['low']:>5}  {r['scale']:>6.2f}  {r['max']:>5.1f}  "
              f"  {r['ic']:+.3f}  {r['n']:>5}")

    # ── Section 4: High-zone / Low-zone median return analysis ───────────────
    print("\n=== ZONE RETURN ANALYSIS (optimal thresholds) ===")
    best = results[0]
    HIGH, LOW, SCALE, MAX_ADJ = best['high'], best['low'], best['scale'], best['max']
    high_zone = [r for r in dataset if r['dxy_score'] > HIGH]
    low_zone  = [r for r in dataset if r['dxy_score'] < LOW]
    mid_zone  = [r for r in dataset if LOW <= r['dxy_score'] <= HIGH]

    def _med(rows):
        vs = [r['fwd_ret_365'] for r in rows]
        return statistics.median(vs) if vs else None

    def _mean(rows):
        vs = [r['fwd_ret_365'] for r in rows]
        return statistics.mean(vs) if vs else None

    print(f"  Best config: HIGH={HIGH}  LOW={LOW}  SCALE={SCALE:.2f}  MAX={MAX_ADJ:.1f}")
    print(f"\n  {'Zone':12s}  {'n':>5}  {'median fwd_365':>15}  {'mean fwd_365':>13}")
    print("  " + "-" * 52)
    print(f"  {'DXY > HIGH':12s}  {len(high_zone):>5}  {(_med(high_zone) or 0):>+14.1f}%  "
          f"{(_mean(high_zone) or 0):>+12.1f}%")
    print(f"  {'DXY neutral':12s}  {len(mid_zone):>5}  {(_med(mid_zone) or 0):>+14.1f}%  "
          f"{(_mean(mid_zone) or 0):>+12.1f}%")
    print(f"  {'DXY < LOW':12s}  {len(low_zone):>5}  {(_med(low_zone) or 0):>+14.1f}%  "
          f"{(_mean(low_zone) or 0):>+12.1f}%")

    # ── Recommendation ────────────────────────────────────────────────────────
    print("\n=== RECOMMENDED CONSTANTS ===")
    print(f"  # Calibrated via tools/backtest_dxy.py ({datetime.date.today()})")
    print(f"  _DXY_HIGH, _DXY_LOW, _DXY_SCALE, _DXY_MAX = "
          f"{HIGH}, {LOW}, {SCALE:.2f}, {MAX_ADJ:.1f}")
    print(f"\n  Paste into scraper/scoring_pipeline.py (replaces the provisional values)")

    # Also print provisional vs optimal IC comparison
    prov_adjs = [dxy_adjustment(r['dxy_score'], 65, 35, 0.20, 5.0) for r in dataset]
    ic_prov, _ = _pearson(prov_adjs, all_fwd)
    ic_prov_str = f'{ic_prov:+.3f}' if ic_prov is not None else 'N/A'
    print(f"\n  IC(provisional 65/35/0.20/5.0, fwd_ret_365): {ic_prov_str}")
    print(f"  IC(optimal     {HIGH}/{LOW}/{SCALE:.2f}/{MAX_ADJ:.1f}, fwd_ret_365): {results[0]['ic']:+.3f}")


if __name__ == '__main__':
    main()
