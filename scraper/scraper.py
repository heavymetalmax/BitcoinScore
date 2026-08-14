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
from . import mayer_multiple as mayer_multiple_mod
from . import funding_rate as funding_rate_mod
from .utils import write_json, validate_data
import urllib.request as _urllib_request


def _fill_missing_prices(history: list) -> int:
    """Fetch Kraken daily closes for any entries missing btc_price. Returns filled count."""
    null_dates = [e['date'] for e in history if e.get('btc_price') is None and e.get('date')]
    if not null_dates:
        return 0
    try:
        since_dt = datetime.date.fromisoformat(null_dates[0]) - datetime.timedelta(days=1)
        since_ts = int(datetime.datetime(since_dt.year, since_dt.month, since_dt.day,
                                         tzinfo=datetime.timezone.utc).timestamp())
        url = (f'https://api.kraken.com/0/public/OHLC'
               f'?pair=XBTUSD&interval=1440&since={since_ts}')
        req = _urllib_request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = _urllib_request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        if data.get('error'):
            print('Kraken price fill error:', data['error'])
            return 0
        candles = data['result'].get('XXBTZUSD', [])
        price_map = {}
        for c in candles:
            dt_str = datetime.datetime.fromtimestamp(
                int(c[0]), tz=datetime.timezone.utc).strftime('%Y-%m-%d')
            price_map[dt_str] = round(float(c[4]), 2)  # close price
        filled = 0
        by_date = {e['date']: e for e in history}
        for d in null_dates:
            if d in price_map:
                by_date[d]['btc_price'] = price_map[d]
                filled += 1
        if filled:
            print(f'Backfilled {filled} missing btc_price entries from Kraken')
        return filled
    except Exception as e:
        print(f'Warning: could not backfill missing prices from Kraken: {e}')
        return 0


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + 'Z'


def build_payload():
    price = None
    fg = None
    btc_dominance = None
    failed_live_fetches = []

    # ── BTC price: Binance primary, CMC secondary, CoinGecko fallback ──────────
    try:
        import urllib.request as _ur
        _resp = _ur.urlopen('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT', timeout=5)
        _bn = __import__('json').loads(_resp.read())
        _bn_price = float(_bn['price'])
        price = {'price': _bn_price, 'change_24h': None}
        print(f"Binance price: ${_bn_price:,.0f}")
    except Exception as e:
        print('Binance price error', e)
    if price is None and cmc_mod.available():
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

    if 'metrics' not in locals():
        metrics = {}

    payload = {
        'timestamp': now_iso(),
        'date': datetime.date.today().isoformat(),
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
        'm2': m2 if 'm2' in locals() else None,
        'failed_live_fetches': failed_live_fetches,
        'stale_metrics_count': len(failed_live_fetches),
        'metrics': {
            # merge our collected metrics dict with static ones
            **(metrics if 'metrics' in locals() else {}),
            'fear_greed': {'value': fg.get('avg_7d', fg.get('latest')) if fg else None, 'latest': fg.get('latest') if fg else None, 'avg_7d': fg.get('avg_7d') if fg else None, 'label': fg.get('label') if fg else None, 'source': fg.get('label', 'Alternative.me') if fg else None, 'updated': now_iso()},
            'cipherb': {'value': None, 'source': 'Local', 'updated': now_iso()},
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
            pct = lth_val / 21_000_000 * 100 if lth_val > 100 else lth_val
            print(f'LTH Supply: {pct:.2f}% (raw={lth_val:.0f} BTC)' if lth_val > 100 else f'LTH Supply: {lth_val:.2f}%')
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

    # ── V3 pipeline (zone_forecast, onchain/tech sub-scores only) ────────────
    from .scoring_pipeline import run_scoring_pipeline
    p = run_scoring_pipeline(p, _build_metric_history)

    # ── V4 final score ─────────────────────────────────────────────────────────
    try:
        import datetime as _datetime
        from .score import compute_score as _v4_score

        _m = p.get('metrics', {})
        _lth_raw = (_m.get('lth_supply_pct') or {}).get('value')
        _lth = (_lth_raw / 21_000_000 * 100) if (_lth_raw and _lth_raw > 100) else _lth_raw

        _raw_v4 = {
            'nupl':           p.get('nupl'),
            'mvrv':           p.get('mvrv_z_score'),
            'rhodl_ratio':    p.get('rhodl_ratio'),
            'cvdd_ratio':     p.get('cvdd_ratio'),
            'asopr':          p.get('asopr'),
            'puell_multiple': (_m.get('puell_multiple') or {}).get('value'),
            'mayer_multiple': ((_m.get('mayer_multiple') or {}).get('value') or {}).get('value'),
            'fear_greed':     p.get('fear_greed'),
            'm2_mom':         p.get('m2_yoy'),
            'yield_curve':    (_m.get('yield_curve') or {}).get('value'),
            'lth_supply_pct': _lth,
            'dxy':            (_m.get('dxy') or {}).get('value'),
            'funding_rate':   (_m.get('funding_rate') or {}).get('value'),
            'pi_cycle':       p.get('pi_gap_pct'),
            'cipherb':        (_m.get('cipherb') or {}).get('value'),
            'etf_flows':      (_m.get('etf_flows') or {}).get('value'),
        }

        _today_d = _datetime.date.today()
        _hist_raw = json.load(open('data/history/scores.json', encoding='utf-8'))
        _sh = [
            (e['date'], e['final_score'], e.get('phase'), e.get('w_bot'))
            for e in _hist_raw
            if e.get('date') < _today_d.isoformat() and e.get('final_score') is not None
        ]

        _v4 = _v4_score(
            raw_metrics=_raw_v4,
            target_date=_today_d,
            prev_scores=None,
            scores_history=_sh,
            btc_price=p.get('btc_price'),
        )

        p['final_score'] = _v4['final_score']
        p['onchain_score'] = _v4['onchain_avg']
        p['tech_score'] = _v4['tech_avg']
        p['v3_w_bot']    = _v4['w_bot']
        p['v3_w_top']    = _v4['w_top']
        p['v3_phase']    = _v4['phase']
        p['v3_normalized_scores'] = _v4['normalized_scores']
        p['v3_utilities'] = _v4['utilities']
        p['v5_score'] = _v4.get('v5_score')
        p['v5b_score'] = _v4.get('v5b_score')
        p['v5b_validated'] = _v4.get('v5b_validated', False)
        p['market_regime'] = _v4.get('market_regime')
        p['decision_suppressed'] = _v4.get('decision_suppressed', True)
        p['decision_suppression_reason'] = _v4.get('decision_suppression_reason')
        if isinstance(p.get('data_quality'), dict):
            p['data_quality']['status'] = _v4.get('score_status', 'invalid')
            p['data_quality']['basket_coverage'] = _v4.get('basket_coverage', {})
        from .scoring_pipeline import sync_public_names
        sync_public_names(p, _v4)
        print(f'V4: score={_v4["final_score"]}  phase={_v4["phase"]}'
              f'  w_bot={_v4["w_bot"]:.2f}  w_top={_v4["w_top"]:.2f}')
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f'Warning: V4 compute_score failed, keeping V3 score — {e}')

    # ── Upsert today into scores.json with full raw metrics ───────────────────
    try:
        from datetime import datetime as _dt, timedelta

        with open('data/history/scores.json') as f:
            history = json.load(f)

        # Recover gaps from previous data.json score_history
        _history_dates = {e.get('date') for e in history}
        for _e in prev_score_history:
            _d = _e.get('date')
            if _d and _d not in _history_dates:
                history.append({'date': _d, 'final_score': _e.get('score'),
                                'btc_price': _e.get('price')})
                _history_dates.add(_d)
        history.sort(key=lambda e: e.get('date', ''))

        today = _dt.now().date()
        today_str = today.isoformat()

        _m = p.get('metrics', {})
        _lth_raw2 = (_m.get('lth_supply_pct') or {}).get('value')
        _lth2 = (_lth_raw2 / 21_000_000 * 100) if (_lth_raw2 and _lth_raw2 > 100) else _lth_raw2
        _mayer_v = (_m.get('mayer_multiple') or {}).get('value') or {}
        _mayer = _mayer_v.get('value') if isinstance(_mayer_v, dict) else _mayer_v

        today_rec = {
            'date':           today_str,
            'final_score':    p.get('final_score'),
            'phase':          p.get('v3_phase'),
            'w_bot':          p.get('v3_w_bot'),
            'w_top':          p.get('v3_w_top'),
            'btc_price':      p.get('btc_price'),
            'nupl':           p.get('nupl'),
            'mvrv':           p.get('mvrv_z_score'),
            'rhodl_ratio':    p.get('rhodl_ratio'),
            'cvdd_ratio':     p.get('cvdd_ratio'),
            'asopr':          p.get('asopr'),
            'puell':          (_m.get('puell_multiple') or {}).get('value'),
            'mayer_multiple': _mayer,
            'fear_greed':     p.get('fear_greed'),
            'funding_rate':   ((_m.get('funding_rate') or {}).get('value') or {}).get('avg_7d'),
            'cipherb_daily':  ((_m.get('cipherb') or {}).get('value') or {}).get('daily_score'),
            'pi_gap_pct':     p.get('pi_gap_pct'),
            'm2_yoy':         p.get('m2_yoy'),
            'lth_supply_pct': _lth2,
            'yield_curve':    (_m.get('yield_curve') or {}).get('value'),
            'dxy':            (_m.get('dxy') or {}).get('value'),
            'v5b_score':      p.get('v5b_score'),
            'market_regime':  p.get('market_regime'),
            'source':         'live_v4',
        }

        idx = next((i for i, e in enumerate(history) if e.get('date') == today_str), None)
        if idx is not None:
            history[idx].update(today_rec)
        else:
            history.append(today_rec)

        yesterday = today - timedelta(days=1)
        seven_days_ago = today - timedelta(days=7)
        for entry in history:
            d = entry.get('date')
            if not d:
                continue
            _ed = _dt.fromisoformat(d).date()
            if _ed == yesterday:
                p['prev_day'] = {'date': d, 'final_score': entry.get('final_score')}
            elif _ed == seven_days_ago:
                p['prev_week'] = {'date': d, 'final_score': entry.get('final_score')}

        _fill_missing_prices(history)

        cutoff = today - timedelta(days=90)
        p['score_history'] = [
            {'date': e['date'], 'score': e.get('final_score'), 'price': e.get('btc_price')}
            for e in history
            if e.get('date') and _dt.fromisoformat(e['date']).date() >= cutoff
        ]

        write_json('data/history/scores.json', history)
    except Exception as e:
        print(f"Warning: Failed to update scores.json: {e}")

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

    # ── Sync data.json to web/ unconditionally (screenshot may fail) ────────────
    import shutil as _shutil
    if os.path.exists('data/data.json'):
        _shutil.copy2('data/data.json', 'web/data.json')
    if os.path.exists('data/data_exp.json'):
        _shutil.copy2('data/data_exp.json', 'web/data_exp.json')

    # ── Regenerate sparklines so web/sparklines.json stays fresh ─────────────
    try:
        from tools.generate_sparklines import main as _gen_sparklines
        _gen_sparklines()
    except Exception as e:
        print(f'WARNING: Sparklines generation failed: {e}')

    # ── Generate screenshot preview of the Stressless dashboard ────────────────
    try:
        from .screenshot import generate_stressless_screenshot
        generate_stressless_screenshot()
    except Exception as e:
        print('WARNING: Stressless screenshot generation failed:', e)

    # ── Validate pipeline output — fail loudly if data is inconsistent ────────
    try:
        from tools.validate_output import validate
        validate()
    except SystemExit:
        raise  # propagate validation failures
    except Exception as e:
        print(f'WARNING: output validation error: {e}')


if __name__ == '__main__':
    main()
