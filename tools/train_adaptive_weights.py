"""
Compute Score Processor v2 parameters:
  - top_centroid:    mean wave-vector at confirmed cycle tops
  - bottom_centroid: mean wave-vector at confirmed cycle bottoms
  - metric_stds:     per-dimension std across all market conditions
                     (used for diagonal Mahalanobis distance — downweights
                      high-volatility tech metrics automatically)

Wave vector = [current_scores × 11] + [delta_scores × 11]
  where delta = score_now - score_N_days_ago (N depends on metric speed)

Distance-based scoring:
  d_top    = mahalanobis_diag(current_wave, top_centroid,    metric_stds)
  d_bottom = mahalanobis_diag(current_wave, bottom_centroid, metric_stds)
  score    = d_bottom / (d_top + d_bottom) × 100

Run: python3 tools/train_adaptive_weights.py
Output: data/adaptive_weights.json
"""
import sys, os, json, math, datetime
sys.path.insert(0, '.')

from tools.backtest import load_data, compute_at

# ── Centroid window ──────────────────────────────────────────────────────────
# Each labeled date is expanded to a ±WINDOW_DAYS range to stabilise the centroid.
# A 22D space is underdetermined with 5 single points; ±14d gives ~100+ vectors.
WINDOW_DAYS = 14

def dates_around(date_str, window=WINDOW_DAYS):
    """Yield dates in [date - window, date + window]."""
    center = datetime.date.fromisoformat(date_str)
    for delta in range(-window, window + 1):
        yield (center + datetime.timedelta(days=delta)).isoformat()


# ── Labeled extremes ──────────────────────────────────────────────────────────
TOP_DATES = [
    '2021-04-14',   # Spring 2021 ATH
    '2021-11-10',   # Nov 2021 ATH — NUPL/MVRV/CB all extreme
    '2024-03-14',   # 2024 cycle ATH
    '2025-07-17',   # CB weekly peak (price not peak yet, wave peak)
    '2025-09-29',   # Price ATH (CB already diverging — intentionally moderate)
]

BOTTOM_DATES = [
    '2018-12-15',   # 2018 cycle bottom — confirmed
    '2022-06-18',   # Capitulation
    '2022-11-21',   # FTX bottom — deepest in cycle
]

# ── Metric ordering and trajectory lookbacks ──────────────────────────────────
# Lookback period reflects natural signal frequency of each metric:
#   SLOW  (monthly cycle rhythm): 30d
#   MEDIUM (biweekly):            14d
#   FAST  (weekly momentum):       7d
#   MACRO (quarterly):            60d
METRIC_ORDER = [
    'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'mayer_multiple',
    'asopr',
    'etf_flows',
    'cipherb_weekly', 'cipherb_daily', 'fear_greed',
    'm2_yoy', 'yield_curve_spread',
]

METRIC_LOOKBACK = {
    'nupl':               30,
    'mvrv_z_score':       30,
    'rhodl_ratio':        30,
    'cvdd_ratio':         30,
    'mayer_multiple':     30,
    'asopr':              14,
    'etf_flows':          14,
    'cipherb_weekly':     14,   # weekly bar — 2-week trajectory
    'cipherb_daily':       7,   # daily — 1-week trajectory
    'fear_greed':          7,
    'm2_yoy':             60,
    'yield_curve_spread': 60,
}

# ── Wave vector ───────────────────────────────────────────────────────────────

def wave_vector(date_str, series):
    """
    Compute 22-dim wave vector for a date:
      [score_0..score_10, delta_0..delta_10]

    Uses backtest.compute_at() for point-in-time accuracy.
    Returns None if date has insufficient data.
    """
    td = datetime.date.fromisoformat(date_str)
    r  = compute_at(td, series)
    scores = r['scores']

    # For each metric: get score from lookback date
    prev = {}
    for metric, lb in METRIC_LOOKBACK.items():
        prev_td = td - datetime.timedelta(days=lb)
        pr = compute_at(prev_td, series)
        prev[metric] = pr['scores'].get(metric)

    vec = []
    for m in METRIC_ORDER:
        vec.append(scores.get(m))                     # position
    for m in METRIC_ORDER:
        s_now  = scores.get(m)
        s_prev = prev.get(m)
        if s_now is not None and s_prev is not None:
            vec.append(s_now - s_prev)                # direction (delta)
        else:
            vec.append(None)

    # Require at least 6 of 11 position scores to be non-None
    n_present = sum(1 for v in vec[:11] if v is not None)
    return vec if n_present >= 6 else None


def centroid(vectors):
    """Element-wise mean, ignoring None."""
    valid = [v for v in vectors if v is not None]
    if not valid:
        return None
    n = len(valid[0])
    result = []
    for i in range(n):
        vals = [v[i] for v in valid if v[i] is not None]
        result.append(round(sum(vals) / len(vals), 4) if vals else None)
    return result


# ── Per-dimension std for Mahalanobis ────────────────────────────────────────

def compute_std_vector(series, sample_every=14):
    """Sample wave vectors every N days across full history; return per-dim std.

    High-volatility metrics (CipherB, Fear&Greed) get large sigma → smaller
    normalised distance → less influence on the score.
    Stable on-chain metrics (NUPL, MVRV) get small sigma → more influence.
    """
    # Use NUPL dates as the backbone (longest series, 2010+)
    nupl_dates = sorted(d for d, _ in series.get('nupl', []))
    # Restrict to post-2016 where most metrics exist
    all_dates = [d for d in nupl_dates if d >= '2016-01-01']
    sample_dates = [d for i, d in enumerate(all_dates) if i % sample_every == 0]

    print(f"  Sampling {len(sample_dates)} dates for σ estimation...")
    all_vecs = []
    for d in sample_dates:
        try:
            v = wave_vector(d, series)
            if v is not None:
                all_vecs.append(v)
        except Exception:
            pass

    if len(all_vecs) < 10:
        return None

    n_dims = len(all_vecs[0])
    stds = []
    for i in range(n_dims):
        vals = [v[i] for v in all_vecs if v[i] is not None]
        if len(vals) < 5:
            stds.append(1.0)
            continue
        mean = sum(vals) / len(vals)
        var  = sum((x - mean) ** 2 for x in vals) / len(vals)
        stds.append(max(var ** 0.5, 0.5))   # floor at 0.5 to avoid division by near-zero
    return [round(s, 4) for s in stds]


# ── Distance scoring ──────────────────────────────────────────────────────────

def mahalanobis_diag(v1, v2, stds):
    """Standardised Euclidean (diagonal Mahalanobis): each dim divided by sigma."""
    if not v1 or not v2 or not stds:
        return None
    total, n = 0.0, 0
    for a, b, s in zip(v1, v2, stds):
        if a is not None and b is not None and s > 0:
            total += ((a - b) / s) ** 2
            n += 1
    return math.sqrt(total / n) if n > 0 else None


def v2_score(wave_vec, top_c, bottom_c, stds=None):
    """score = d_bottom / (d_top + d_bottom) × 100"""
    dist = mahalanobis_diag if stds else (lambda v1, v2, _: _euclidean_plain(v1, v2))

    def _euclidean_plain(v1, v2):
        total, n = 0.0, 0
        for a, b in zip(v1, v2):
            if a is not None and b is not None:
                total += (a - b) ** 2
                n += 1
        return math.sqrt(total / n) if n > 0 else None

    d_top    = mahalanobis_diag(wave_vec, top_c, stds) if stds else _euclidean_plain(wave_vec, top_c)
    d_bottom = mahalanobis_diag(wave_vec, bottom_c, stds) if stds else _euclidean_plain(wave_vec, bottom_c)
    if d_top is None or d_bottom is None:
        return None
    denom = d_top + d_bottom
    return round(d_bottom / denom * 100) if denom > 0 else 50


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Loading series data...")
    series = load_data()

    print(f"\nComputing wave vectors (±{WINDOW_DAYS}d windows) for {len(TOP_DATES)} tops + {len(BOTTOM_DATES)} bottoms...")

    top_vecs, bottom_vecs = [], []

    for d in TOP_DATES:
        count = 0
        for dd in dates_around(d):
            v = wave_vector(dd, series)
            if v:
                top_vecs.append(v)
                count += 1
        print(f"  TOP    {d}: {count} vectors")

    for d in BOTTOM_DATES:
        count = 0
        for dd in dates_around(d):
            v = wave_vector(dd, series)
            if v:
                bottom_vecs.append(v)
                count += 1
        print(f"  BOTTOM {d}: {count} vectors")

    top_c    = centroid(top_vecs)
    bottom_c = centroid(bottom_vecs)

    if not top_c or not bottom_c:
        print("\nERROR: insufficient data for centroids"); return

    print(f"\nTop centroid    (pos): {[round(x) if x else None for x in top_c[:11]]}")
    print(f"Bottom centroid (pos): {[round(x) if x else None for x in bottom_c[:11]]}")

    # ── Per-dimension σ for Mahalanobis ──────────────────────────────────────
    print("\nComputing per-dimension σ from historical sample...")
    stds = compute_std_vector(series)
    if stds:
        score_stds = [round(s, 2) for s in stds[:11]]
        delta_stds = [round(s, 2) for s in stds[11:]]
        print(f"  Score σ: {score_stds}")
        print(f"  Delta σ: {delta_stds}")
    else:
        print("  WARNING: could not compute σ — falling back to Euclidean")

    def dist(v, c):
        return mahalanobis_diag(v, c, stds) if stds else None

    # ── Calibration anchors for phase signals ─────────────────────────────────
    top_d_tops, top_d_bots = [], []
    bot_d_tops, bot_d_bots = [], []
    for d in TOP_DATES:
        v = wave_vector(d, series)
        if v:
            top_d_tops.append(dist(v, top_c))
            top_d_bots.append(dist(v, bottom_c))
    for d in BOTTOM_DATES:
        v = wave_vector(d, series)
        if v:
            bot_d_tops.append(dist(v, top_c))
            bot_d_bots.append(dist(v, bottom_c))

    cal = {
        'd_top_min': round(min(top_d_tops), 4),
        'd_top_max': round(max(bot_d_tops), 4),
        'd_bot_min': round(min(bot_d_bots), 4),
        'd_bot_max': round(max(top_d_bots), 4),
    }
    print(f"\nCalibration anchors: {cal}")

    # ── Validate ──────────────────────────────────────────────────────────────
    print(f"\nValidation — v2 + phase signals at labeled dates:")
    print(f"  {'Date':<12} {'Role':<8} {'v2':>4} {'top_sig':>8} {'bot_sig':>8}  phase")
    print("  " + "─" * 65)
    for d, role in [(d, 'TOP') for d in TOP_DATES] + [(d, 'BTM') for d in BOTTOM_DATES]:
        v = wave_vector(d, series)
        if not v:
            print(f"  {d:<12} {role:<8}   —   MISSING"); continue
        s    = v2_score(v, top_c, bottom_c, stds)
        dt   = dist(v, top_c)
        db   = dist(v, bottom_c)
        tsig = max(0, min(100, round((cal['d_top_max'] - dt) / (cal['d_top_max'] - cal['d_top_min']) * 100)))
        bsig = max(0, min(100, round((cal['d_bot_max'] - db) / (cal['d_bot_max'] - cal['d_bot_min']) * 100)))
        diff = tsig - bsig
        if   tsig > 50 and tsig > bsig: phase = 'TOP'
        elif bsig > 50 and bsig > tsig: phase = 'BOTTOM'
        elif diff >  10:                phase = 'BULL'
        elif diff < -10:                phase = 'BEAR'
        else:                           phase = 'NEUTRAL'
        print(f"  {d:<12} {role:<8} {s or 0:>4}  {tsig:>6}%  {bsig:>6}%  {phase}")

    # ── Save ──────────────────────────────────────────────────────────────────
    out = {
        'version':        3,
        'top_centroid':   top_c,
        'bottom_centroid':bottom_c,
        'metric_stds':    stds,
        'calibration':    cal,
        'metric_order':   METRIC_ORDER,
        'metric_lookback':METRIC_LOOKBACK,
        'top_dates':      TOP_DATES,
        'bottom_dates':   BOTTOM_DATES,
        'generated':      datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'formula':        'd_bottom / (d_top + d_bottom) × 100  [mahalanobis-diag, calibrated anchors]',
        'vector_dims':    f'{len(METRIC_ORDER)} scores + {len(METRIC_ORDER)} deltas = {2*len(METRIC_ORDER)}d',
    }
    path = 'data/adaptive_weights.json'
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved → {path}")


if __name__ == '__main__':
    main()
