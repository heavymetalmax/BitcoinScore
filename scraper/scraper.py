#!/usr/bin/env python3
"""Main scraper skeleton: collects metrics and writes data/data.json

This is a minimal implementation that calls API wrappers and placeholder
MacroMicro functions. Complete Playwright scraping and OCR fallback later.
"""
import datetime
import os
import json
import time

# Allow running this file directly as a script (python scraper/scraper.py)
# while keeping package-style imports working. When executed as a script,
# __package__ may be None which breaks absolute imports like "from scraper...".
if __name__ == '__main__' and __package__ is None:
    import sys
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if parent not in sys.path:
        sys.path.insert(0, parent)
    __package__ = 'scraper'
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed yet; run: pip install python-dotenv
from .coingecko import get_price
from .alternative_me import get_fear_greed
from . import cmc as cmc_mod
from .nupl import get_nupl
from .mvrv import get_mvrv
from .m2_metric import get_m2
from .yield_curve_metric import get_yield_curve
from .utils import human_visit
from .cipherb import get_cipherb
from .smc import get_smc
from . import mayer_multiple as mayer_multiple_mod
from . import funding_rate as funding_rate_mod
from .utils import write_json, validate_data


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + 'Z'


def build_payload():
    price = None
    fg = None
    btc_dominance = None
    failed_live_fetches = []

    # ── BTC price: CMC primary, CoinGecko fallback ────────────────────────────
    if cmc_mod.available():
        try:
            cmc_price = cmc_mod.get_btc_price()
            if cmc_price and cmc_price.get('price'):
                price = {'price': cmc_price['price'], 'change_24h': cmc_price.get('change_24h')}
                print(f"CMC price: ${price['price']:,.0f}")
        except Exception as e:
            print('CMC price error', e)
    if price is None:
        try:
            price = get_price()
        except Exception as e:
            print('CoinGecko error', e)

    if price is None:
        failed_live_fetches.append('btc_price')

    # ── Fear & Greed: CMC primary, alternative.me fallback ───────────────────
    if cmc_mod.available():
        try:
            cmc_fg = cmc_mod.get_fear_greed()
            if cmc_fg is not None:
                fg = cmc_fg  # already {'latest': X, 'avg_7d': Y, 'label': 'CMC'}
                print(f"CMC F&G: {cmc_fg.get('latest')} (avg7d={cmc_fg.get('avg_7d')})")
        except Exception as e:
            print('CMC F&G error', e)
    if fg is None:
        try:
            fg = get_fear_greed()
        except Exception as e:
            print('Fear&Greed error', e)

    if fg is None:
        failed_live_fetches.append('fear_greed')

    # ── Global metrics (BTC dominance) ───────────────────────────────────────
    if cmc_mod.available():
        try:
            gm = cmc_mod.get_global_metrics()
            if gm and gm.get('btc_dominance') is not None:
                btc_dominance = round(gm['btc_dominance'], 2)
                print(f'CMC BTC dominance: {btc_dominance}%')
        except Exception as e:
            print('CMC global metrics error', e)

    # MacroMicro: avoid spamming requests. If cached MacroMicro metrics exist and are
    # recent (<=24h) and valid, reuse them instead of live-fetching.
    nupl = None
    mvrv = None
    prev_metrics = None
    cache_path = 'data/data.json'
    try:
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as hf:
                prev = json.load(hf)
                prev_ts = prev.get('timestamp')
                if prev_ts:
                    prev_dt = datetime.datetime.fromisoformat(prev_ts.replace('Z', '+00:00'))
                    age = (datetime.datetime.now(datetime.timezone.utc) - prev_dt).total_seconds()
                else:
                    age = None
                prev_metrics = prev.get('metrics', {}) if isinstance(prev, dict) else {}
                # consider cached valid if NUPL present and not null
                cached_nupl = None
                if prev_metrics and 'nupl' in prev_metrics and prev_metrics['nupl'].get('value') is not None:
                    cached_nupl = prev_metrics['nupl'].get('value')
                if cached_nupl is not None and age is not None and age <= 43200:
                    # reuse cached MacroMicro metrics
                    try:
                        nupl = float(cached_nupl)
                    except Exception:
                        nupl = cached_nupl
                    if 'mvrv' in prev_metrics and prev_metrics['mvrv'].get('value') is not None:
                        mvrv = prev_metrics['mvrv'].get('value')
    except Exception:
        nupl = None
        mvrv = None

    # If not reused from cache, perform live fetches sequentially per metric.
    import random
    from . import nupl as nupl_mod
    from . import mvrv as mvrv_mod
    from . import m2_metric as m2_mod
    from . import yield_curve_metric as yc_mod
    from . import cvdd as cvdd_mod
    from . import rhodl as rhodl_mod
    from . import rainbow as rainbow_mod
    from . import asopr as asopr_mod
    from . import etf_flows as etf_flows_mod
    from . import dxy_metric as dxy_mod
    from .utils import is_valid_metric

    metric_specs = [
        ('nupl', nupl_mod.get_nupl, None, lambda r: r),
        ('mvrv', mvrv_mod.get_mvrv, None, lambda r: r),
        ('m2', m2_mod.get_m2, None, lambda r: r),
        ('yield_curve', yc_mod.get_yield_curve, None, lambda r: r),
        ('cvdd_ratio', cvdd_mod.get_cvdd_ratio, None, lambda r: r),
        ('rhodl_ratio', rhodl_mod.get_rhodl_ratio, None, lambda r: r),
        ('asopr', asopr_mod.get_asopr, None, lambda r: r),
        ('etf_flows', etf_flows_mod.get_etf_flows, None, lambda r: r),
        ('dxy', dxy_mod.get_dxy, None, lambda r: r),
    ]

    for name, fn, visit_url, extractor in metric_specs:
        # prefer cached value when available and recent
        cached_val = None
        if prev_metrics and name in prev_metrics and prev_metrics[name].get('value') is not None and 'age' in locals() and age is not None and age <= 43200 and os.environ.get('FORCE_LIVE') != '1':
            if not (name == 'm2' and prev_metrics[name].get('source') == 'BMP'):
                _cv = prev_metrics[name].get('value')
                if name == 'etf_flows' and isinstance(_cv, dict) and _cv.get('date'):
                    try:
                        import datetime as _dt_etf
                        _etf_age_days = (_dt_etf.date.today() - _dt_etf.date.fromisoformat(_cv['date'])).days
                        if _etf_age_days > 3:
                            _cv = None  # data is >3 days stale — force live fetch
                    except Exception:
                        pass
                cached_val = _cv
        if cached_val is not None:
            val = cached_val
        else:
            # open humanized visit if requested and url provided
            try:
                if os.environ.get('HUMANIZE_SCRAPE') and visit_url:
                    human_visit(visit_url, wait_seconds=6)
            except Exception:
                pass
            try:
                res = fn()
            except Exception as e:
                res = None
            # extract numeric candidate
            try:
                val = extractor(res)
            except Exception:
                val = res

            # validate
            if not is_valid_metric(name, val):
                val = None

            # Fallback to cached value (even if stale) on live fetch failure
            is_fallback = False
            if val is None:
                if prev_metrics and name in prev_metrics and prev_metrics[name].get('value') is not None:
                    val = prev_metrics[name].get('value')
                    print(f"Warning: live fetch failed for {name}, fell back to cached value: {val}")
                    is_fallback = True
                else:
                    print(f"Warning: live fetch failed for {name} and no cached value available")

            if is_fallback or val is None:
                failed_live_fetches.append(name)

            # short randomized pause between scrapers
            time.sleep(1 + random.random() * 2)

        # assign to payload fields where relevant
        if name == 'nupl':
            nupl = val
        if name == 'mvrv':
            mvrv = val

        if name == 'm2':
            m2 = val
        if name == 'yield_curve':
            yield_curve = val
        if name == 'cvdd_ratio':
            cvdd_ratio = val
        if name == 'rhodl_ratio':
            rhodl_ratio = val
        if name == 'asopr':
            asopr = val
        if name == 'etf_flows':
            etf_flows = val

        # save into metrics dict placeholder (later merged into payload)
        if 'metrics' not in locals():
            metrics = {}
        src = 'MacroMicro' if name == 'm2' else 'Farside' if name == 'etf_flows' else 'FRED' if name == 'dxy' else 'BMP'
        metrics[name] = {'value': val, 'source': src if val is not None else None, 'updated': now_iso()}

    # Rainbow band — separate call (returns dict, not scalar)
    rainbow_band = None
    try:
        rainbow_band = rainbow_mod.get_rainbow_band()
    except Exception as e:
        print(f'rainbow band failed: {e}')
        failed_live_fetches.append('rainbow_band')
    if 'metrics' not in locals():
        metrics = {}
    metrics['rainbow_band'] = {'value': rainbow_band, 'source': 'BMP', 'updated': now_iso()}
    
    payload = {
        'timestamp': now_iso(),
        'btc_price': price['price'] if price else None,
        'btc_dominance': btc_dominance,
        'fear_greed': fg.get('avg_7d', fg.get('latest')) if fg else None,
        'fear_greed_label': fg.get('label') if fg else None,
        'nupl': nupl,
        'mvrv_z_score': mvrv,
        'cvdd_ratio': cvdd_ratio if 'cvdd_ratio' in locals() else None,
        'rhodl_ratio': rhodl_ratio if 'rhodl_ratio' in locals() else None,
        'asopr': asopr if 'asopr' in locals() else None,
        'etf_flows': etf_flows if 'etf_flows' in locals() else None,
        'rainbow_band': rainbow_band if 'rainbow_band' in locals() else None,
        'm2': m2 if 'm2' in locals() else None,
        'failed_live_fetches': failed_live_fetches,
        'metrics': {
            # merge our collected metrics dict with static ones
            **(metrics if 'metrics' in locals() else {}),
            'fear_greed': {'latest': fg.get('latest') if fg else None, 'avg_7d': fg.get('avg_7d') if fg else None, 'label': fg.get('label') if fg else None, 'source': fg.get('label', 'Alternative.me') if fg else None, 'updated': now_iso()},
            'cipherb': {'value': None, 'source': 'Local', 'updated': now_iso()},
            'smc': {'value': None, 'source': 'Local', 'updated': now_iso()},
            'mayer_multiple': {'value': None, 'source': 'Local', 'updated': now_iso()},
            'funding_rate': {'value': None, 'source': 'Binance', 'updated': now_iso()}
        }
    }
    return payload


def _build_metric_history(n_days=365):
    """Last n_days of per-metric 0-100 scores for sparklines in data_exp.json."""
    KEYS = ['nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'asopr',
            'cipherb', 'mayer_multiple', 'etf_flows', 'fear_greed',
            'yield_curve_spread', 'm2_yoy']
    cutoff = (datetime.date.today() - datetime.timedelta(days=n_days)).isoformat()
    bf = {}
    bf_path = 'data/history/backfill_scores.json'
    if os.path.exists(bf_path):
        raw = json.load(open(bf_path, encoding='utf-8'))
        for row in (raw.get('series', raw) if isinstance(raw, dict) else raw):
            d = row.get('date', '')[:10]
            if d >= cutoff:
                bf[d] = row.get('mapped', {})
    dv = {}
    dv_path = 'data/history/daily_vector.json'
    if os.path.exists(dv_path):
        for row in json.load(open(dv_path, encoding='utf-8')):
            d = row.get('date', '')[:10]
            if d >= cutoff:
                dv[d] = row.get('mapped', {})
    dates = sorted(set(bf) | set(dv))
    out = {'dates': dates}
    for k in KEYS:
        out[k] = [(dv.get(d, {}).get(k) if dv.get(d, {}).get(k) is not None
                   else bf.get(d, {}).get(k)) for d in dates]
    return out


def main():
    p = build_payload()
    
    # Load previous metrics for fallback and preserve score_history across commits
    prev_metrics = {}
    prev_score_history = []
    try:
        if os.path.exists('data/data.json'):
            with open('data/data.json', 'r', encoding='utf-8') as hf:
                prev_data = json.load(hf)
                prev_metrics = prev_data.get('metrics', {})
                prev_score_history = prev_data.get('score_history', [])
    except Exception as e:
        print('Error loading prev_metrics for fallback:', e)

    # Basic validation and clipping for suspicious values
    if p.get('nupl') is not None:
        if p['nupl'] < -50 or p['nupl'] > 100:
            print('Warning: NUPL out of range, clamping:', p['nupl'])
            p['nupl'] = max(-50, min(100, p['nupl']))
    if p.get('mvrv_z_score') is not None:
        if p['mvrv_z_score'] < -10 or p['mvrv_z_score'] > 20:
            print('Warning: MVRV out of expected range, clamping:', p['mvrv_z_score'])
            p['mvrv_z_score'] = max(-10, min(20, p['mvrv_z_score']))

    missing = validate_data(p)
    if missing:
        print('Warning: missing keys in payload:', missing)
    write_json('data/data.json', p)
    print('Wrote data/data.json')

    # Populate cipherb metric (run locally) and rewrite data.json with the value
    try:
        cb = get_cipherb('BTCUSDT')
        if cb and cb.get('last'):
            # store only under metrics.cipherb to avoid top-level duplication
            p['metrics']['cipherb'] = {'value': cb.get('last'), 'source': 'Local', 'updated': now_iso()}
            write_json('data/data.json', p)
            print('Updated data/data.json with cipherb')
        else:
            raise ValueError("cipherb empty or invalid")
    except Exception as e:
        print('cipherb error', e)
        if prev_metrics.get('cipherb') and prev_metrics['cipherb'].get('value') is not None:
            p['metrics']['cipherb'] = prev_metrics['cipherb']
            print(f"Warning: cipherb failed, fell back to cached value")
        if 'cipherb' not in p['failed_live_fetches']:
            p['failed_live_fetches'].append('cipherb')
        write_json('data/data.json', p)

    # Populate SMC metric (Price vs Support) — kept for historical reference, not used in scoring
    try:
        smc = get_smc('BTCUSDT', timeframe='1w', size=10)
        if smc and smc.get('last'):
            p['metrics']['smc'] = {'value': smc.get('last'), 'source': 'Local', 'updated': now_iso()}
            write_json('data/data.json', p)
            print('Updated data/data.json with smc')
        else:
            raise ValueError("smc empty or invalid")
    except Exception as e:
        print('smc error', e)
        if prev_metrics.get('smc') and prev_metrics['smc'].get('value') is not None:
            p['metrics']['smc'] = prev_metrics['smc']
            print(f"Warning: smc failed, fell back to cached value")
        if 'smc' not in p['failed_live_fetches']:
            p['failed_live_fetches'].append('smc')
        write_json('data/data.json', p)

    # Populate Mayer Multiple metric (Price / 200DMA on daily)
    try:
        mm = mayer_multiple_mod.get_mayer_multiple()
        if mm is not None:
            p['metrics']['mayer_multiple'] = {'value': mm, 'source': 'Kraken', 'updated': now_iso()}
            write_json('data/data.json', p)
            print(f"Updated data/data.json with mayer_multiple: val={mm['value']}  score={mm['score']}")
        else:
            raise ValueError("mayer_multiple is None")
    except Exception as e:
        print('mayer_multiple error', e)
        if prev_metrics.get('mayer_multiple') and prev_metrics['mayer_multiple'].get('value') is not None:
            p['metrics']['mayer_multiple'] = prev_metrics['mayer_multiple']
            print(f"Warning: mayer_multiple failed, fell back to cached value")
        if 'mayer_multiple' not in p['failed_live_fetches']:
            p['failed_live_fetches'].append('mayer_multiple')
        write_json('data/data.json', p)

    # Populate Funding Rate metric (Binance Futures, 7-day avg)
    try:
        fr = funding_rate_mod.get_funding_rate('BTCUSDT')
        if fr is not None:
            p['metrics']['funding_rate'] = {'value': fr, 'source': fr.get('source', 'Bybit'), 'updated': now_iso()}
            write_json('data/data.json', p)
            print(f"Updated data/data.json with funding_rate: avg_7d={fr['avg_7d']}%  score={fr['score']}")
        else:
            raise ValueError("funding_rate is None")
    except Exception as e:
        print('funding_rate error', e)
        if prev_metrics.get('funding_rate') and prev_metrics['funding_rate'].get('value') is not None:
            p['metrics']['funding_rate'] = prev_metrics['funding_rate']
            print(f"Warning: funding_rate failed, fell back to cached value")
        if 'funding_rate' not in p['failed_live_fetches']:
            p['failed_live_fetches'].append('funding_rate')
        write_json('data/data.json', p)

    # ── Cycle metrics (data collection only — not scored yet) ────────────────
    # Halving Cycle Day: days since last halving (April 19, 2024)
    try:
        _halving = datetime.date(2024, 4, 19)
        halving_cycle_day = (datetime.date.today() - _halving).days
        p['metrics']['halving_cycle_day'] = {'value': halving_cycle_day, 'source': 'Local', 'updated': now_iso()}
        print(f'Halving cycle day: {halving_cycle_day}')
    except Exception as e:
        print('halving_cycle_day error', e)

    # Pi Cycle Top (111DMA vs 2×350DMA)
    try:
        from . import pi_cycle as pi_cycle_mod
        pc = pi_cycle_mod.get_pi_cycle()
        if pc is not None:
            p['metrics']['pi_cycle'] = {'value': pc, 'source': 'Kraken', 'updated': now_iso()}
            write_json('data/data.json', p)
            print(f"Pi Cycle: gap={pc['gap_pct']}%  cross={pc['cross']}  score={pc['score']}")
        else:
            raise ValueError("pi_cycle is None")
    except Exception as e:
        print('pi_cycle error', e)
        if prev_metrics.get('pi_cycle') and prev_metrics['pi_cycle'].get('value') is not None:
            p['metrics']['pi_cycle'] = prev_metrics['pi_cycle']
            print(f"Warning: pi_cycle failed, fell back to cached value")
        if 'pi_cycle' not in p['failed_live_fetches']:
            p['failed_live_fetches'].append('pi_cycle')
        write_json('data/data.json', p)

    # Puell Multiple (miner revenue / 365d MA)
    try:
        from . import puell as puell_mod
        puell_val = puell_mod.get_puell_multiple()
        if puell_val is not None:
            p['metrics']['puell_multiple'] = {'value': puell_val, 'source': 'BMP', 'updated': now_iso()}
            write_json('data/data.json', p)
            print(f'Puell Multiple: {puell_val}')
        else:
            raise ValueError("puell_multiple is None")
    except Exception as e:
        print('puell_multiple error', e)
        if prev_metrics.get('puell_multiple') and prev_metrics['puell_multiple'].get('value') is not None:
            p['metrics']['puell_multiple'] = prev_metrics['puell_multiple']
            print(f"Warning: puell_multiple failed, fell back to cached value")
        if 'puell_multiple' not in p['failed_live_fetches']:
            p['failed_live_fetches'].append('puell_multiple')
        write_json('data/data.json', p)

    # LTH Supply % (long-term holder share of total supply)
    try:
        from . import lth_supply as lth_mod
        lth_val = lth_mod.get_lth_supply_pct()
        if lth_val is not None:
            p['metrics']['lth_supply_pct'] = {'value': lth_val, 'source': 'BMP', 'updated': now_iso()}
            write_json('data/data.json', p)
            print(f'LTH Supply: {lth_val}%')
        else:
            raise ValueError("lth_supply_pct is None")
    except Exception as e:
        print('lth_supply_pct error', e)
        if prev_metrics.get('lth_supply_pct') and prev_metrics['lth_supply_pct'].get('value') is not None:
            p['metrics']['lth_supply_pct'] = prev_metrics['lth_supply_pct']
            print(f"Warning: lth_supply_pct failed, fell back to cached value")
        if 'lth_supply_pct' not in p['failed_live_fetches']:
            p['failed_live_fetches'].append('lth_supply_pct')
        write_json('data/data.json', p)

    from .history_writer import write_metric_histories
    write_metric_histories(p)

    from .scoring_pipeline import run_scoring_pipeline
    p = run_scoring_pipeline(p, _build_metric_history)

    # ── Add prev_day, prev_week, and score_history fields ─────────────────────
    try:
        from datetime import datetime, timedelta

        # Load current history
        with open('data/history/scores.json') as f:
            history = json.load(f)

        # Recover gaps: merge any dates from the previous data.json score_history
        # that are missing from scores.json (happens when home server doesn't commit
        # scores.json — each new run would otherwise lose days between commits)
        _prev_sh = prev_score_history
        _history_dates = {e.get('date') for e in history}
        for _e in _prev_sh:
            _d = _e.get('date')
            if _d and _d not in _history_dates:
                history.append({'date': _d, 'final_score': _e.get('score')})
                _history_dates.add(_d)
        history.sort(key=lambda e: e.get('date', ''))

        today = datetime.now().date()
        today_str = today.isoformat()

        # Append today's score if not already there
        if not any(entry.get('date') == today_str for entry in history):
            history.append({
                'date': today_str,
                'final_score': p.get('final_score'),
                'phase': p.get('v3_phase') or (p.get('phase', {}).get('phase', 'UNKNOWN') if isinstance(p.get('phase'), dict) else p.get('phase', 'UNKNOWN'))
            })
        
        # Find yesterday's entry
        yesterday = today - timedelta(days=1)
        for entry in history:
            if entry.get('date') and datetime.fromisoformat(entry['date']).date() == yesterday:
                p['prev_day'] = {'date': entry['date'], 'final_score': entry.get('final_score')}
                break
        
        # Find 7 days ago
        seven_days_ago = today - timedelta(days=7)
        for entry in history:
            if entry.get('date') and datetime.fromisoformat(entry['date']).date() == seven_days_ago:
                p['prev_week'] = {'date': entry['date'], 'final_score': entry.get('final_score')}
                break
        
        # Build score_history (last 90 days)
        score_history = []
        cutoff = today - timedelta(days=90)
        for entry in history:
            if entry.get('date') and datetime.fromisoformat(entry['date']).date() >= cutoff:
                score_history.append({'date': entry['date'], 'score': entry.get('final_score'), 'price': entry.get('btc_price')})
        if score_history:
            p['score_history'] = score_history
        
        # Write updated history back
        write_json('data/history/scores.json', history)
    except Exception as e:
        print(f"Warning: Failed to populate prev_day/prev_week/score_history: {e}")

    # Write the updated data with history fields
    write_json('data/data.json', p)


    # ── Append the FULL daily indicator vector to history ──────────────────────
    # Builds the per-day training matrix (raw inputs + mapped 0-100 scores +
    # group scores) needed for any future weight-calibration / ML work.
    # See tools/ml_weight_probe.py for why this matters.
    try:
        from .scoring import build_slider_map
        m = p.get('metrics', {})
        def raw(key):
            o = m.get(key)
            return o.get('value') if isinstance(o, dict) and 'value' in o else o
        def sclr(o, *keys):
            """Flatten a possibly-nested metric to a single representative scalar."""
            if isinstance(o, dict):
                for k in keys:
                    if o.get(k) is not None:
                        return o[k]
                return None
            return o
        mapped = build_slider_map(m)
        row = {
            'date':        p['timestamp'][:10],
            'timestamp':   p['timestamp'],
            'btc_price':   p.get('btc_price'),
            'onchain_score': p.get('onchain_score'),
            'tech_score':    p.get('tech_score'),
            'final_score':   p.get('final_score'),
            # inverted zone prices logged daily, to backtest these against the
            # actual future price once enough history accumulates
            'buy_zone_price':  (p.get('zone_forecast') or {}).get('buy', {}).get('price'),
            'sell_zone_price': (p.get('zone_forecast') or {}).get('sell', {}).get('price'),
            # raw indicator values (flattened to scalars)
            'raw': {
                'nupl':           sclr(raw('nupl')),
                'mvrv':           sclr(raw('mvrv')),
                'cvdd_ratio':     sclr(raw('cvdd_ratio')),
                'rhodl_ratio':    sclr(raw('rhodl_ratio')),
                'asopr':          sclr(raw('asopr')),
                'cipherb':        sclr(raw('cipherb'), 'weekly_score'),
                'mayer_multiple': sclr(raw('mayer_multiple'), 'value'),
                'etf_flows':      sclr(raw('etf_flows'), 'value'),
                'fear_greed':     p.get('fear_greed'),
                'yield_curve':    sclr(raw('yield_curve')),
                'm2_yoy':         p.get('m2_mom', raw('m2_mom')),
                'funding_rate':   sclr(raw('funding_rate'), 'avg_7d', 'latest'),
                'smc':            sclr(raw('smc'), 'position'),
                # cycle metrics (collected for future use)
                'halving_cycle_day': sclr(raw('halving_cycle_day')),
                'pi_cycle_gap_pct':  sclr(raw('pi_cycle'), 'gap_pct'),
                'pi_cycle_cross':    sclr(raw('pi_cycle'), 'cross'),
                'puell_multiple':    sclr(raw('puell_multiple')),
                'lth_supply_pct':    sclr(raw('lth_supply_pct')),
            },
            # mapped 0-100 risk scores per indicator (what the weights act on)
            'mapped': mapped,
        }
        dv_path = 'data/history/daily_vector.json'
        os.makedirs(os.path.dirname(dv_path), exist_ok=True)
        if os.path.exists(dv_path):
            with open(dv_path, 'r', encoding='utf-8') as hf:
                dv = json.load(hf)
        else:
            dv = []
        # de-dup: replace any existing entry for the same date
        dv = [r for r in dv if r.get('date') != row['date']]
        dv.append(row)
        dv.sort(key=lambda r: r.get('date', ''))
        write_json(dv_path, dv)
        print(f"Wrote {dv_path} ({len(dv)} daily vectors)")
    except Exception as e:
        print('Failed to write daily vector history:', e)

    # ── Download fresh Pexels wallpapers (real + abstract) ───────────────────
    try:
        from .wallpaper import update_wallpapers
        update_wallpapers(final_score=p.get('final_score'), web_dir='web')
    except Exception as e:
        print('Wallpaper update failed:', e)

    # ── Generate screenshot preview of the Stressless dashboard ────────────────
    try:
        from .screenshot import generate_stressless_screenshot
        generate_stressless_screenshot()
    except Exception as e:
        print('Stressless screenshot generation failed:', e)


if __name__ == '__main__':
    main()
