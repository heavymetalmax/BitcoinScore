"""Metric normalizer for BitcoinScore.

Scales raw metrics to uniform 0-100 scores.
To avoid lookahead bias, it supports point-in-time (causal) rolling percentiles
using only data available up to target_date.
"""
import datetime
import math
from scraper.scoring import (
    map_nupl, map_mvrv, map_rhodl, map_cvdd, map_asopr,
    map_fear_greed, map_m2, map_yield_curve, map_mayer_multiple,
    map_etf_flow, map_funding, map_dxy, map_lth_supply, _load_metric_history,
    ADAPTIVE_METRICS, ADAPTIVE_BLEND, ADAPTIVE_WIN_YEARS, _PCTILE_DIVISOR
)
from scraper.scoring_v2 import map_puell

# Trailing window for rolling percentile rank
ADAPTIVE_WIN_DAYS = int(ADAPTIVE_WIN_YEARS * 365)


def get_causal_history(metric, target_date=None):
    """Get sorted historical series [(date_str, value)] up to target_date."""
    pts = _load_metric_history(metric)
    if target_date is None:
        return pts
    
    cutoff_str = target_date.isoformat() if isinstance(target_date, datetime.date) else str(target_date)
    return [p for p in pts if p[0] <= cutoff_str]


def compute_causal_percentile(metric, value, target_date=None):
    """Compute 0-100 percentile rank of value using only data up to target_date."""
    if value is None:
        return None
        
    while isinstance(value, dict):
        value = value.get('value')
    if not isinstance(value, (int, float)):
        return None
        
    pts = get_causal_history(metric, target_date)
    if len(pts) < 24:
        return None
        
    # Calculate start of window (4 years back from target date or last point in causal history)
    last_date_str = pts[-1][0][:10]
    last_date = datetime.date.fromisoformat(last_date_str)
    
    lo = (last_date - datetime.timedelta(days=ADAPTIVE_WIN_DAYS)).isoformat()
    win = [v for (d, v) in pts if d[:10] >= lo]
    
    if len(win) < 12:
        return None
        
    divisor = _PCTILE_DIVISOR.get(metric, 1)
    cmp_val = value / divisor
    
    le = sum(1 for v in win if v <= cmp_val)
    return round(le / len(win) * 100)


def normalize_metric(metric, value, target_date=None):
    """Normalize a raw metric value to a 0-100 score."""
    if value is None:
        return None
        
    # Unwrap dictionaries if necessary
    metric_val = value
    if isinstance(metric_val, dict) and 'value' in metric_val:
        metric_val = metric_val['value']
        
    # Select appropriate fixed mapping function
    fixed_score = None
    if metric == 'nupl':
        fixed_score = map_nupl(metric_val)
    elif metric in ('mvrv', 'mvrv_z_score'):
        fixed_score = map_mvrv(metric_val)
    elif metric == 'rhodl_ratio':
        fixed_score = map_rhodl(metric_val)
    elif metric == 'cvdd_ratio':
        fixed_score = map_cvdd(metric_val)
    elif metric == 'asopr':
        fixed_score = map_asopr(metric_val)
    elif metric == 'puell':
        fixed_score = map_puell(metric_val)
    elif metric == 'mayer_multiple':
        fixed_score = map_mayer_multiple(metric_val)
    elif metric == 'fear_greed':
        fixed_score = map_fear_greed(metric_val)
    elif metric in ('m2_yoy', 'm2_mom'):
        fixed_score = map_m2(metric_val)
    elif metric in ('yield_curve_spread', 'yield_curve'):
        fixed_score = map_yield_curve(metric_val)
    elif metric == 'etf_flows':
        fixed_score = map_etf_flow(metric_val)
    elif metric == 'funding_rate':
        fixed_score = map_funding(metric_val)
    elif metric == 'dxy':
        fixed_score = map_dxy(metric_val)
    elif metric == 'lth_supply':
        fixed_score = map_lth_supply(metric_val)

    # If the metric is adaptive, blend with rolling percentile
    # Translate metric names to history key if needed
    hist_key = metric
    if metric == 'mvrv_z_score':
        hist_key = 'mvrv'
        
    if hist_key in ADAPTIVE_METRICS and fixed_score is not None:
        pct = compute_causal_percentile(hist_key, metric_val, target_date)
        if pct is not None:
            return round(ADAPTIVE_BLEND * pct + (1 - ADAPTIVE_BLEND) * fixed_score)
            
    return fixed_score
