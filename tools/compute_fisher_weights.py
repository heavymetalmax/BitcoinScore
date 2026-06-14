"""
Compute Fisher-separation-based optimal metric weights.

Fisher separation per metric:
  separation(m) = |mean(score_m at TOPs) - mean(score_m at BOTTOMs)| / std(score_m)
  weight(m) = separation(m) / sum(all separations)

Single metric pool — no forced OC/TECH split.
Excluded from Fisher (fixed weights): etf_flows=0.05, yield_curve_spread=0.0

Also computes wr_boost = wr_sep / v2_sep (Fisher separations for WR and V2 scores),
written into the same output file so orchestrator.py can load it at startup.

Run: python3 tools/compute_fisher_weights.py
Output: data/fisher_weights.json
"""
import sys, os, json, datetime, statistics
sys.path.insert(0, '.')

from tools.train_adaptive_weights import TOP_DATES, BOTTOM_DATES, dates_around
from tools.backtest import load_data, compute_at, v2_at
from scraper.wave_resonance import compute_wave_resonance

# ── Pool and fixed weights ───────────────────────────────────────────────────

FISHER_POOL = [
    'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio',
    'asopr', 'cipherb', 'mayer_multiple', 'fear_greed', 'm2_yoy',
]

FIXED_WEIGHTS = {
    'etf_flows':          0.05,
    'yield_curve_spread': 0.0,
}

# V1 effective weights (OC group 50% × metric_w, TECH group 50% × metric_w)
V1_EFFECTIVE = {
    'nupl':               0.50 * 0.30,
    'mvrv_z_score':       0.50 * 0.20,
    'rhodl_ratio':        0.50 * 0.20,
    'cvdd_ratio':         0.50 * 0.15,
    'asopr':              0.50 * 0.15,
    'cipherb':            0.50 * 0.40,
    'mayer_multiple':     0.50 * 0.20,
    'fear_greed':         0.50 * 0.10,
    'etf_flows':          0.50 * 0.10,
    'yield_curve_spread': 0.50 * 0.10,
    'm2_yoy':             0.50 * 0.10,
}


# ── Score collection ─────────────────────────────────────────────────────────

def collect_scores(dates, series, window=14):
    """
    For each labeled date, expand ±window days, call compute_at(), collect
    scores for each metric in FISHER_POOL.
    Returns {metric: [score_values...]}.
    """
    pool_scores = {m: [] for m in FISHER_POOL}
    for date_str in dates:
        for dd in dates_around(date_str, window=window):
            td = datetime.date.fromisoformat(dd)
            try:
                r = compute_at(td, series)
                s = r['scores']
                for m in FISHER_POOL:
                    v = s.get(m)
                    if v is not None:
                        pool_scores[m].append(v)
            except Exception:
                pass
    return pool_scores


# ── Historical std ───────────────────────────────────────────────────────────

def compute_std(series, sample_every=14):
    """
    Sample every Nth date from post-2016 NUPL backbone, compute_at() for each,
    return {metric: std} with a minimum floor of 1.0.
    """
    nupl_dates = sorted(d for d, _ in series.get('nupl', []))
    all_dates  = [d for d in nupl_dates if d >= '2016-01-01']
    sample_dates = [d for i, d in enumerate(all_dates) if i % sample_every == 0]

    print(f"  Sampling {len(sample_dates)} dates for σ estimation...")
    metric_vals = {m: [] for m in FISHER_POOL}

    for d in sample_dates:
        td = datetime.date.fromisoformat(d)
        try:
            r = compute_at(td, series)
            s = r['scores']
            for m in FISHER_POOL:
                v = s.get(m)
                if v is not None:
                    metric_vals[m].append(v)
        except Exception:
            pass

    result = {}
    for m in FISHER_POOL:
        vals = metric_vals[m]
        if len(vals) >= 3:
            result[m] = max(statistics.pstdev(vals), 1.0)
        else:
            result[m] = 1.0
    return result


# ── WR / V2 Fisher separation ────────────────────────────────────────────────

def _collect_wr_v2(dates, series, window=14):
    """Collect WR and V2 scores at ±window days around each labeled date."""
    wr_vals, v2_vals = [], []
    for date_str in dates:
        for dd in dates_around(date_str, window=window):
            td = datetime.date.fromisoformat(dd)
            wr = compute_wave_resonance(dd)
            if wr.get('score') is not None:
                wr_vals.append(wr['score'])
            try:
                v2_diag, _v2_full = v2_at(td, series)
                if v2_diag is not None:
                    v2_vals.append(v2_diag)
            except Exception:
                pass
    return wr_vals, v2_vals


def _sep(top_vals, bot_vals):
    """Fisher separation: |mean_top - mean_bot| / std_combined."""
    if not top_vals or not bot_vals:
        return 0.0
    combined = top_vals + bot_vals
    if len(combined) < 2:
        return 0.0
    mt = sum(top_vals) / len(top_vals)
    mb = sum(bot_vals) / len(bot_vals)
    std = statistics.pstdev(combined)
    return abs(mt - mb) / (std + 1e-9)


def _compute_wr_boost(series, window=14):
    """Return (wr_boost, wr_sep, v2_sep) using Fisher separation on TOP/BOTTOM dates."""
    print(f"  Collecting WR/V2 scores at ±{window}d windows...")
    wr_tops, v2_tops = _collect_wr_v2(TOP_DATES,    series, window)
    wr_bots, v2_bots = _collect_wr_v2(BOTTOM_DATES, series, window)
    print(f"  WR samples: tops={len(wr_tops)} bots={len(wr_bots)}")
    print(f"  V2 samples: tops={len(v2_tops)} bots={len(v2_bots)}")
    wr_sep = _sep(wr_tops, wr_bots)
    v2_sep = _sep(v2_tops, v2_bots)
    boost = wr_sep / (v2_sep + 1e-9)
    if boost < 0.1:
        print(f"  WARNING: wr_boost={boost:.4f} too low — falling back to 1.0")
        boost = 1.0
    return boost, wr_sep, v2_sep


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Loading series data...")
    series = load_data()

    print(f"\nCollecting scores at ±14d windows for {len(TOP_DATES)} tops, {len(BOTTOM_DATES)} bottoms...")
    top_scores    = collect_scores(TOP_DATES,    series)
    bottom_scores = collect_scores(BOTTOM_DATES, series)

    print(f"\nSample counts — tops: {[len(top_scores[m]) for m in FISHER_POOL]}")
    print(f"Sample counts — bots: {[len(bottom_scores[m]) for m in FISHER_POOL]}")

    print("\nComputing historical σ...")
    stds = compute_std(series)

    # ── Fisher separations ────────────────────────────────────────────────────
    separations = {}
    top_means   = {}
    bot_means   = {}

    for m in FISHER_POOL:
        t_vals = top_scores[m]
        b_vals = bottom_scores[m]
        if not t_vals or not b_vals:
            separations[m] = 0.0
            top_means[m]   = None
            bot_means[m]   = None
            continue
        mt = sum(t_vals) / len(t_vals)
        mb = sum(b_vals) / len(b_vals)
        top_means[m] = round(mt, 2)
        bot_means[m] = round(mb, 2)
        separations[m] = abs(mt - mb) / stds[m]

    total_sep = sum(separations.values())
    if total_sep == 0:
        print("ERROR: all separations are zero"); return

    # ── Fisher weights (pool fraction; fixed portion subtracted) ─────────────
    fixed_total = sum(FIXED_WEIGHTS.values())
    available   = 1.0 - fixed_total        # 0.95 for pool after fixing etf=0.05, yc=0.0

    raw_fisher = {m: separations[m] / total_sep for m in FISHER_POOL}
    fisher_weights = {m: round(raw_fisher[m] * available, 4) for m in FISHER_POOL}

    # Assemble full weight dict (pool + fixed)
    all_weights = dict(fisher_weights)
    all_weights.update(FIXED_WEIGHTS)

    # ── Print comparison table ────────────────────────────────────────────────
    header = f"{'metric':<22} {'mean_T':>7} {'mean_B':>7} {'sep':>6} {'v1_eff':>7} {'fisher':>7}"
    print(f"\n{header}")
    print("─" * len(header))
    for m in FISHER_POOL:
        v1w   = V1_EFFECTIVE.get(m, 0.0)
        fw    = fisher_weights[m]
        mt_s  = f"{top_means[m]:>7.1f}" if top_means[m] is not None else "    N/A"
        mb_s  = f"{bot_means[m]:>7.1f}" if bot_means[m] is not None else "    N/A"
        sep_s = f"{separations[m]:>6.2f}"
        print(f"{m:<22} {mt_s} {mb_s} {sep_s} {v1w:>7.3f} {fw:>7.4f}")
    for m, w in FIXED_WEIGHTS.items():
        v1w = V1_EFFECTIVE.get(m, 0.0)
        print(f"{m:<22} {'(fixed)':>7} {'(fixed)':>7} {'—':>6} {v1w:>7.3f} {w:>7.4f}")

    # ── WR / V2 separation for wr_boost ──────────────────────────────────────
    print("\nComputing WR and V2 Fisher separations for wr_boost...")
    wr_boost, wr_sep_val, v2_sep_val = _compute_wr_boost(series)
    print(f"  wr_sep={wr_sep_val:.4f}  v2_sep={v2_sep_val:.4f}  wr_boost={wr_boost:.4f}")

    # ── Save ──────────────────────────────────────────────────────────────────
    out = {
        'version':      1,
        'generated':    datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'weights':      all_weights,
        'separations':  {m: round(separations[m], 4) for m in FISHER_POOL},
        'top_means':    top_means,
        'bottom_means': bot_means,
        'top_dates':    TOP_DATES,
        'bottom_dates': BOTTOM_DATES,
        'formula':      '|mean_top - mean_bottom| / std_historical; normalized to available pool weight',
        'fixed_weights': FIXED_WEIGHTS,
        'fisher_pool':  FISHER_POOL,
        'wr_boost':     round(wr_boost, 4),
        'wr_sep':       round(wr_sep_val, 4),
        'v2_sep':       round(v2_sep_val, 4),
    }
    path = 'data/fisher_weights.json'
    os.makedirs('data', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved → {path}")
    print(f"Sum of all weights: {sum(all_weights.values()):.4f}")


if __name__ == '__main__':
    main()
