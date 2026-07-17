"""
Historical score lookup for wave vector delta computation.

Used by score_processor_v2() and phase_signals() to obtain prev_scores:
  - OC metrics (nupl, mvrv, rhodl, cvdd, mayer, asopr): scores.json (ONE database)
  - cipherb_weekly: cipherb_btcusdt_1w.json
  - cipherb_daily:  scores.json (cipherb_daily field)
  - fear_greed, etf_flows: daily_vector.json (recent only)
  - m2_yoy: scores.json
"""
import os
import json
import bisect
import datetime

_HIST_PATH   = os.path.join('data', 'history', 'scores.json')
_WEEKLY_PATH = os.path.join('data', 'history', 'cipherb_btcusdt_1w.json')
_DV_PATH     = os.path.join('data', 'history', 'daily_vector.json')

_MAX_STALE = 45   # days — beyond this treat metric as absent

_uh_cache  = None
_wk_cache  = None
_dv_cache  = None


def _load_uh():
    global _uh_cache
    if _uh_cache is None:
        try:
            rows = json.load(open(_HIST_PATH, encoding='utf-8'))
            _uh_cache = (
                [r['date'] for r in rows],
                rows,
            )
        except Exception:
            _uh_cache = ([], [])
    return _uh_cache


def _load_wk():
    global _wk_cache
    if _wk_cache is None:
        try:
            raw = json.load(open(_WEEKLY_PATH, encoding='utf-8'))
            rows = raw if isinstance(raw, list) else raw.get('series', [])
            pairs = sorted(
                (datetime.datetime.fromtimestamp(r['timestamp'], tz=datetime.timezone.utc).date(),
                 r.get('weekly_score'))
                for r in rows if r.get('timestamp') and r.get('weekly_score') is not None
            )
            _wk_cache = pairs
        except Exception:
            _wk_cache = []
    return _wk_cache


def _load_dv():
    global _dv_cache
    if _dv_cache is None:
        try:
            rows = json.load(open(_DV_PATH, encoding='utf-8'))
            _dv_cache = {r['date']: r.get('mapped', {}) for r in rows}
        except Exception:
            _dv_cache = {}
    return _dv_cache


def _nearest_uh(target_date):
    """Return scores.json row nearest to target_date, or None if too stale."""
    dates, rows = _load_uh()
    if not dates:
        return None
    td = target_date.isoformat()
    idx = bisect.bisect_right(dates, td) - 1
    if idx < 0:
        return None
    row = rows[idx]
    stale = (target_date - datetime.date.fromisoformat(row['date'][:10])).days
    return row if stale <= _MAX_STALE else None


def _nearest_wk(target_date):
    """Return (date, weekly_score) nearest to target_date, or None."""
    pairs = _load_wk()
    if not pairs:
        return None
    dates = [p[0] for p in pairs]
    idx = bisect.bisect_right(dates, target_date) - 1
    if idx < 0:
        return None
    d, score = pairs[idx]
    stale = (target_date - d).days
    return score if stale <= _MAX_STALE else None


def _nearest_dv(target_date):
    """Return mapped dict from daily_vector for nearest date, or {}."""
    dv = _load_dv()
    if not dv:
        return {}
    td = target_date.isoformat()
    keys = sorted(dv.keys())
    idx = bisect.bisect_right(keys, td) - 1
    if idx < 0:
        return {}
    key = keys[idx]
    stale = (target_date - datetime.date.fromisoformat(key)).days
    return dv[key] if stale <= _MAX_STALE else {}


def get_prev_scores(target_date: datetime.date) -> dict:
    """
    Return mapped scores (same schema as score_from_raw()) for target_date.
    Missing metrics return None in the dict (handled gracefully by euclidean).
    """
    from .scoring import score_from_raw

    row = _nearest_uh(target_date)
    dv  = _nearest_dv(target_date)
    wk  = _nearest_wk(target_date)

    if row:
        raw = {
            'nupl':           row.get('nupl'),
            'mvrv':           row.get('mvrv'),
            'rhodl_ratio':    row.get('rhodl_ratio'),
            'cvdd_ratio':     row.get('cvdd_ratio'),
            'mayer_multiple': row.get('mayer_multiple'),
            'asopr':          row.get('asopr'),
            'm2_yoy':         row.get('m2_yoy'),
            'yield_curve_spread': row.get('yield_curve_spread') or row.get('yield_curve'),
            'cipherb': {
                'weekly_score': wk,
                'daily_score':  row.get('cipherb_daily'),
            } if (wk is not None or row.get('cipherb_daily') is not None) else None,
            'etf_flows':   dv.get('etf_flows'),
            'fear_greed':  dv.get('fear_greed'),
        }
    else:
        raw = {
            'cipherb': {'weekly_score': wk, 'daily_score': None} if wk is not None else None,
            'etf_flows':  dv.get('etf_flows'),
            'fear_greed': dv.get('fear_greed'),
        }

    return score_from_raw(raw)


def build_prev_scores_for_wave(today: datetime.date, metric_lookback: dict) -> dict:
    """
    Build prev_scores dict for score_processor_v2 / phase_signals.
    Each metric gets its score from (today - METRIC_LOOKBACK[metric]) days ago.
    Caches per unique lookback to avoid redundant file reads.
    """
    unique_lbs = set(metric_lookback.values())
    lb_scores  = {lb: get_prev_scores(today - datetime.timedelta(days=lb))
                  for lb in unique_lbs}
    return {m: lb_scores[lb].get(m) for m, lb in metric_lookback.items()}
