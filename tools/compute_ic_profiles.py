"""IC-based utility profiles — replaces hardcoded RELEVANCE_PROFILES.

For each metric m and phase p, computes:
  IC(m, p) = Pearson(norm_score_m | phase=p, fwd_return_N_days)
  utility(m, p) = sigmoid(-IC × ALPHA) ∈ [0.1, 1.0]

Sign convention:
  Negative IC → high utility  (high score → low future return = correct risk signal)
  Positive IC → low utility   (momentum contamination)
  IC ≈ 0     → utility ≈ 0.5 (no signal)

Phase-relevant horizons reflect the score's definition ("safe to buy for long-term"):
  BOTTOM  → 365d  (long-term entry quality)
  NEUTRAL → 270d  (intermediate)
  TOP     → 180d  (catching the rollover before damage)

ALPHA = 6.0 maps:
  IC = -0.30 → utility ≈ 0.86
  IC =  0.00 → utility = 0.50
  IC = +0.30 → utility ≈ 0.14
"""

import math
import statistics

ALPHA = 6.0           # single hyperparameter
MIN_SAMPLES = 30      # minimum phase-conditioned samples to trust IC

PHASE_HORIZONS = {
    'BOTTOM':  'fwd_ret_365',
    'NEUTRAL': 'fwd_ret_270',
    'TOP':     'fwd_ret_180',
}


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def ic_to_utility(ic: float, alpha: float = ALPHA) -> float:
    """Map Pearson IC ∈ [-1, 1] to utility ∈ [0.1, 1.0]."""
    return round(max(0.1, min(1.0, _sigmoid(-ic * alpha))), 3)


def _pearson(xs, ys):
    """Pearson r, or None if insufficient data."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < MIN_SAMPLES:
        return None
    xs2, ys2 = zip(*pairs)
    mx, my = statistics.mean(xs2), statistics.mean(ys2)
    sx = statistics.stdev(xs2)
    sy = statistics.stdev(ys2)
    if sx < 1e-9 or sy < 1e-9:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs2, ys2)) / (len(xs2) * sx * sy)


def compute_ic_profiles(dataset, verbose: bool = True) -> dict:
    """Compute IC-based utility profiles from the precomputed dataset.

    Parameters
    ----------
    dataset : list[dict]
        Each row must have: ``normalized`` (dict of metric→score),
        ``phase`` ('BOTTOM'|'NEUTRAL'|'TOP'), and the horizon keys
        ``fwd_ret_180``, ``fwd_ret_270``, ``fwd_ret_365``.

    Returns
    -------
    dict  — same shape as RELEVANCE_PROFILES:
        {metric: {'BOTTOM': float, 'NEUTRAL': float, 'TOP': float}}
    """
    all_metrics: set = set()
    for row in dataset:
        all_metrics.update(row.get('normalized', {}).keys())

    profiles: dict = {}
    ic_table: dict = {}   # {metric: {phase: ic}} — stored for diagnostics

    if verbose:
        print("\n=== IC-BASED UTILITY PROFILES ===")
        print(f"{'Metric':22s} {'Phase':8s} {'n':>5} {'IC':>8}  {'Utility':>8}  note")
        print("-" * 70)

    for metric in sorted(all_metrics):
        profiles[metric] = {}
        ic_table[metric] = {}

        for phase, horizon_key in PHASE_HORIZONS.items():
            phase_rows = [r for r in dataset if r.get('phase') == phase]

            scores  = [r['normalized'].get(metric) for r in phase_rows]
            returns = [r.get(horizon_key) for r in phase_rows]

            ic = _pearson(scores, returns)
            ic_table[metric][phase] = ic

            if ic is not None:
                utility = ic_to_utility(ic)
                note = '← ✓ risk signal' if ic < -0.10 else ('← ⚠ momentum' if ic > 0.10 else '')
            else:
                utility = 0.5
                note = '← insufficient data'

            profiles[metric][phase] = utility

            if verbose:
                ic_str = f'{ic:+.3f}' if ic is not None else '   N/A'
                n_valid = sum(1 for s, r in zip(scores, returns) if s is not None and r is not None)
                print(f"  {metric:20s} {phase:8s} {n_valid:>5} {ic_str:>8}  {utility:>8.3f}  {note}")

    if verbose:
        print()

    return profiles


def ic_profiles_to_bounds(ic_profiles: dict, slack: float = 0.30):
    """Derive optimisation bounds from IC profiles.

    Each (metric, phase) gets bounds centred on the IC utility ± slack,
    clamped to [0.05, 1.0].  A tighter slack (e.g. 0.20) keeps weights
    closer to the data-driven prior; looser (0.40) gives the optimiser
    more room.
    """
    bounds: dict = {}
    for metric, phases in ic_profiles.items():
        bounds[metric] = {}
        for phase, utility in phases.items():
            low  = max(0.05, utility - slack)
            high = min(1.00, utility + slack)
            bounds[metric][phase] = (low, high)
    return bounds


def print_ic_comparison(ic_profiles: dict, prior_profiles: dict) -> None:
    """Print a side-by-side comparison of IC-derived vs prior profiles."""
    all_metrics = sorted(set(ic_profiles) | set(prior_profiles))
    print(f"\n{'Metric':22s}  {'BOTTOM':^18s}  {'NEUTRAL':^18s}  {'TOP':^18s}")
    print(f"{'':22s}  {'prior→IC':^18s}  {'prior→IC':^18s}  {'prior→IC':^18s}")
    print("-" * 82)
    for m in all_metrics:
        row = f"  {m:20s}"
        for ph in ('BOTTOM', 'NEUTRAL', 'TOP'):
            pr = prior_profiles.get(m, {}).get(ph)
            ic = ic_profiles.get(m, {}).get(ph)
            pr_s = f'{pr:.2f}' if pr is not None else '  ?  '
            ic_s = f'{ic:.2f}' if ic is not None else '  ?  '
            row += f"  {pr_s}→{ic_s}{'':6s}"
        print(row)
