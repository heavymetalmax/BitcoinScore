"""
Bottom confluence score — metric-driven cycle bottom detection.

Calibrates bottom/neutral zones dynamically from confirmed historical cycle lows.
No hardcoded threshold values; only confirmed historical dates are used as labels.

How it works:
  1. For each confirmed bottom date, fetch normalized metric values
  2. bottom_max[metric] = max value seen at any confirmed bottom (worst-case threshold)
  3. neutral_min[metric] = min value seen at any confirmed neutral/bull date
  4. gradient probability: 1.0 at confirmed-bottom level, 0.0 at neutral level
  5. confluence score = mean gradient across 6 reliable metrics

To add a new confirmed bottom: append the date to CONFIRMED_BOTTOM_DATES and delete
data/bottom_confluence_calibration.json so thresholds are recomputed on next run.
"""
import json
import os
import datetime

# Confirmed historical cycle lows — objective facts, not speculation.
# Do NOT add speculative future dates; thresholds are derived from these.
CONFIRMED_BOTTOM_DATES = ['2018-12-15', '2020-03-13', '2022-06-18', '2022-11-21']

# Known neutral/bull-market dates for the "upper bound" of the gradient.
CONFIRMED_NEUTRAL_DATES = ['2019-06-01', '2021-04-14', '2023-06-01', '2025-06-01']

# Metrics validated as reliable bottom indicators (asopr and rhodl_ratio excluded —
# too much overlap between phases).
CONFLUENCE_METRICS = ['nupl', 'mvrv_z_score', 'cvdd_ratio', 'mayer_multiple', 'fear_greed', 'cipherb_weekly']
MIN_CONFLUENCE_METRICS = 4

CACHE_PATH = 'data/bottom_confluence_calibration.json'

# Confluence score ≥ this fraction → Phase = BOTTOM.
# 0.65 captures all confirmed bottoms and the current drawdown;
# early-cycle corrections (< 65%) stay NEUTRAL.
BOTTOM_PHASE_THRESHOLD = 0.65

_calibration_cache: dict | None = None


def _compute_calibration() -> dict:
    """Derive bottom/neutral thresholds from confirmed historical dates using V4 normalizer."""
    import bisect
    from scraper.normalizer import normalize_metric

    scores_path = 'data/history/scores.json'
    by_date: dict[str, dict] = {}
    if os.path.exists(scores_path):
        try:
            with open(scores_path, encoding='utf-8') as f:
                for row in json.load(f):
                    if row.get('date'):
                        by_date[row['date'][:10]] = row
        except Exception:
            pass

    wk_dates: list[str] = []
    wk_vals: list[float] = []
    wk_path = 'data/history/cipherb_btcusdt_1w.json'
    if os.path.exists(wk_path):
        try:
            raw_wk = json.load(open(wk_path, encoding='utf-8'))
            wk_rows = raw_wk.get('series', raw_wk) if isinstance(raw_wk, dict) else raw_wk
            pairs = sorted(
                (r['date'][:10], float(r['weekly_score']))
                for r in wk_rows
                if isinstance(r, dict) and r.get('date') and r.get('weekly_score') is not None
            )
            wk_dates = [p[0] for p in pairs]
            wk_vals = [p[1] for p in pairs]
        except Exception:
            pass

    def _weekly_cb_on(date_str: str, fallback_daily) -> int | None:
        if wk_dates:
            idx = bisect.bisect_right(wk_dates, date_str) - 1
            if idx >= 0:
                return round(wk_vals[idx])
        return round(float(fallback_daily)) if fallback_daily is not None else None

    def _scores_on(date_str: str) -> dict:
        row = by_date.get(date_str, {})
        td = datetime.date.fromisoformat(date_str)
        return {
            'nupl':           normalize_metric('nupl', row.get('nupl'), td),
            'mvrv_z_score':   normalize_metric('mvrv_z_score', row.get('mvrv'), td),
            'cvdd_ratio':     normalize_metric('cvdd_ratio', row.get('cvdd_ratio'), td),
            'mayer_multiple': normalize_metric('mayer_multiple', row.get('mayer_multiple'), td),
            'fear_greed':     normalize_metric('fear_greed', row.get('fear_greed'), td),
            'cipherb_weekly': _weekly_cb_on(date_str, row.get('cipherb_daily')),
        }

    bottom_scores: dict[str, list] = {m: [] for m in CONFLUENCE_METRICS}
    neutral_scores: dict[str, list] = {m: [] for m in CONFLUENCE_METRICS}

    for date_str in CONFIRMED_BOTTOM_DATES:
        sc = _scores_on(date_str)
        for m in CONFLUENCE_METRICS:
            v = sc.get(m)
            if v is not None:
                bottom_scores[m].append(v)

    for date_str in CONFIRMED_NEUTRAL_DATES:
        sc = _scores_on(date_str)
        for m in CONFLUENCE_METRICS:
            v = sc.get(m)
            if v is not None:
                neutral_scores[m].append(v)

    bottom_max = {m: max(v) for m, v in bottom_scores.items() if v}
    neutral_min = {m: min(v) for m, v in neutral_scores.items() if v}

    return {
        'bottom_max': bottom_max,
        'neutral_min': neutral_min,
        'calibrated_from': {
            'bottom_dates': CONFIRMED_BOTTOM_DATES,
            'neutral_dates': CONFIRMED_NEUTRAL_DATES,
        },
    }


def load_calibration(force_recompute: bool = False) -> dict:
    """Return calibration dict, loading from JSON cache or computing fresh."""
    global _calibration_cache
    if _calibration_cache is not None and not force_recompute:
        return _calibration_cache

    if not force_recompute and os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding='utf-8') as f:
                _calibration_cache = json.load(f)
                return _calibration_cache
        except Exception:
            pass

    _calibration_cache = _compute_calibration()
    try:
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(_calibration_cache, f, indent=2)
    except Exception:
        pass
    return _calibration_cache


def _metric_prob(metric: str, value, cal: dict) -> float | None:
    """Gradient probability: 1.0 at confirmed-bottom level, 0.0 at neutral level."""
    if value is None:
        return None
    bot = cal['bottom_max'].get(metric)
    neu = cal['neutral_min'].get(metric)
    if bot is None or neu is None or neu <= bot:
        return None
    if value <= bot:
        return 1.0
    if value >= neu:
        return 0.0
    return 1.0 - (value - bot) / (neu - bot)


def compute_bottom_confluence(normalized_scores: dict, cal: dict | None = None) -> int | None:
    """
    Confluence score 0–100: metric similarity to confirmed historical cycle lows.

    100 = all metrics at confirmed-bottom extremes (deep capitulation).
    0   = all metrics in neutral/bull territory.
    72  ≈ current 2026 drawdown (approaching, not at capitulation).
    """
    if cal is None:
        cal = load_calibration()
    probs = [
        p for m in CONFLUENCE_METRICS
        if (p := _metric_prob(m, normalized_scores.get(m), cal)) is not None
    ]
    if len(probs) < MIN_CONFLUENCE_METRICS:
        return None
    return round(sum(probs) / len(probs) * 100)
