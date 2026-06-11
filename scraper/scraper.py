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
                if cached_nupl is not None and age is not None and age <= 86400:
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
    ]

    for name, fn, visit_url, extractor in metric_specs:
        # prefer cached value when available and recent
        cached_val = None
        if prev_metrics and name in prev_metrics and prev_metrics[name].get('value') is not None and 'age' in locals() and age is not None and age <= 86400 and os.environ.get('FORCE_LIVE') != '1':
            if not (name == 'm2' and prev_metrics[name].get('source') == 'BMP'):
                cached_val = prev_metrics[name].get('value')
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
            if val is None and prev_metrics and name in prev_metrics and prev_metrics[name].get('value') is not None:
                val = prev_metrics[name].get('value')
                print(f"Warning: live fetch failed for {name}, fell back to cached value: {val}")

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
        src = 'MacroMicro' if name == 'm2' else 'Farside' if name == 'etf_flows' else 'BMP'
        metrics[name] = {'value': val, 'source': src if val is not None else None, 'updated': now_iso()}

    # Rainbow band — separate call (returns dict, not scalar)
    rainbow_band = None
    try:
        rainbow_band = rainbow_mod.get_rainbow_band()
    except Exception as e:
        logger.warning('rainbow band failed: %s', e)
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
        'm2': m2,
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


def main():
    p = build_payload()
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
    except Exception as e:
        print('cipherb error', e)

    # Populate SMC metric (Price vs Support) — kept for historical reference, not used in scoring
    try:
        smc = get_smc('BTCUSDT', timeframe='1w', size=10)
        if smc and smc.get('last'):
            p['metrics']['smc'] = {'value': smc.get('last'), 'source': 'Local', 'updated': now_iso()}
            write_json('data/data.json', p)
            print('Updated data/data.json with smc')
    except Exception as e:
        print('smc error', e)

    # Populate Mayer Multiple metric (Price / 200DMA on daily)
    try:
        mm = mayer_multiple_mod.get_mayer_multiple()
        if mm is not None:
            p['metrics']['mayer_multiple'] = {'value': mm, 'source': 'Kraken', 'updated': now_iso()}
            write_json('data/data.json', p)
            print(f"Updated data/data.json with mayer_multiple: val={mm['value']}  score={mm['score']}")
    except Exception as e:
        print('mayer_multiple error', e)

    # Populate Funding Rate metric (Binance Futures, 7-day avg)
    try:
        fr = funding_rate_mod.get_funding_rate('BTCUSDT')
        if fr is not None:
            p['metrics']['funding_rate'] = {'value': fr, 'source': fr.get('source', 'Bybit'), 'updated': now_iso()}
            write_json('data/data.json', p)
            print(f"Updated data/data.json with funding_rate: avg_7d={fr['avg_7d']}%  score={fr['score']}")
    except Exception as e:
        print('funding_rate error', e)

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
    except Exception as e:
        print('pi_cycle error', e)

    # Puell Multiple (miner revenue / 365d MA)
    try:
        from . import puell as puell_mod
        puell_val = puell_mod.get_puell_multiple()
        if puell_val is not None:
            p['metrics']['puell_multiple'] = {'value': puell_val, 'source': 'BMP', 'updated': now_iso()}
            write_json('data/data.json', p)
            print(f'Puell Multiple: {puell_val}')
    except Exception as e:
        print('puell_multiple error', e)

    # LTH Supply % (long-term holder share of total supply)
    try:
        from . import lth_supply as lth_mod
        lth_val = lth_mod.get_lth_supply_pct()
        if lth_val is not None:
            p['metrics']['lth_supply_pct'] = {'value': lth_val, 'source': 'BMP', 'updated': now_iso()}
            write_json('data/data.json', p)
            print(f'LTH Supply: {lth_val}%')
    except Exception as e:
        print('lth_supply_pct error', e)

    # Append M2 snapshot to history
    try:
        hist_path = 'data/history/m2.json'
        os.makedirs(os.path.dirname(hist_path), exist_ok=True)
        if os.path.exists(hist_path):
            with open(hist_path, 'r', encoding='utf-8') as hf:
                hist = json.load(hf)
        else:
            hist = []
        hist.append({'timestamp': p['timestamp'], 'btc_price': p.get('btc_price'), 'm2': p.get('m2')})
        # keep last 730 entries (~2 years daily)
        hist = hist[-730:]
        write_json(hist_path, hist)
        print('Wrote', hist_path)
    except Exception as e:
        print('Failed to write M2 history:', e)

    # Global M2 YoY% — use get_m2() (reads BMP history + US FRED fallback adjustment)
    try:
        from .m2_metric import get_m2 as _get_m2_yoy
        mom_val = _get_m2_yoy()
        m2_src = 'MacroMicro'
        if mom_val is None:
            # Both MacroMicro and FRED failed. M2 YoY moves slowly, so reuse the
            # last known good value rather than writing None: a missing metric
            # silently drops out of the index average and skews every score.
            last_good = None
            pm = (prev_metrics or {}).get('m2_mom') if prev_metrics else None
            if isinstance(pm, dict) and pm.get('value') is not None:
                last_good = pm['value']
            if last_good is None:
                try:
                    with open('data/history/daily_vector.json', encoding='utf-8') as hf:
                        for row in reversed(json.load(hf)):
                            v = (row.get('raw') or {}).get('m2_yoy')
                            if v is not None:
                                last_good = v
                                break
                except Exception:
                    pass
            if last_good is not None:
                mom_val = last_good
                m2_src = 'MacroMicro (cached: live fetch failed)'
                print(f'M2 live fetch failed; reused last known M2 YoY = {last_good}')
            else:
                print('M2 live fetch failed and no cached value found; leaving None')
        if 'metrics' not in p:
            p['metrics'] = {}
        p['metrics']['m2_mom'] = {'value': mom_val, 'source': m2_src, 'updated': now_iso()}
        p['m2_mom'] = mom_val
        write_json('data/data.json', p)
        print(f'Updated data/data.json with M2 MoM = {mom_val} ({m2_src})')
    except Exception as e:
        print('Failed to compute/include M2 MoM:', e)

    # Append Yield Curve snapshot to history for analysis
    try:
        yc_val = p.get('metrics', {}).get('yield_curve', {}).get('value')
        hist_yc_path = 'data/history/yield_curve.json'
        os.makedirs(os.path.dirname(hist_yc_path), exist_ok=True)
        if os.path.exists(hist_yc_path):
            with open(hist_yc_path, 'r', encoding='utf-8') as hf:
                ychist = json.load(hf)
        else:
            ychist = []
        ychist.append({'timestamp': p['timestamp'], 'btc_price': p.get('btc_price'), 'yield_curve': yc_val})
        ychist = ychist[-730:]
        write_json(hist_yc_path, ychist)
        print('Wrote', hist_yc_path)
    except Exception as e:
        print('Failed to write Yield Curve history:', e)

    # Append CipherB snapshot to history
    try:
        cipherb_val = None
        # use only metrics.cipherb (no top-level duplication)
        cipherb_val = p.get('metrics', {}).get('cipherb', {}).get('value')
        hist_c_path = 'data/history/cipherb.json'
        os.makedirs(os.path.dirname(hist_c_path), exist_ok=True)
        if os.path.exists(hist_c_path):
            with open(hist_c_path, 'r', encoding='utf-8') as hf:
                chist = json.load(hf)
        else:
            chist = []
        chist.append({'timestamp': p['timestamp'], 'btc_price': p.get('btc_price'), 'cipherb': cipherb_val})
        chist = chist[-730:]
        write_json(hist_c_path, chist)
        print('Wrote', hist_c_path)
    except Exception as e:
        print('Failed to write CipherB history:', e)

    # Append SMC snapshot to history
    try:
        smc_val = None
        try:
            smc_val = p.get('metrics', {}).get('smc', {}).get('value')
        except Exception:
            smc_val = None
        hist_s_path = 'data/history/smc.json'
        os.makedirs(os.path.dirname(hist_s_path), exist_ok=True)
        if os.path.exists(hist_s_path):
            with open(hist_s_path, 'r', encoding='utf-8') as hf:
                shist = json.load(hf)
        else:
            shist = []
        shist.append({'timestamp': p['timestamp'], 'btc_price': p.get('btc_price'), 'smc': smc_val})
        shist = shist[-730:]
        write_json(hist_s_path, shist)
        print('Wrote', hist_s_path)
    except Exception as e:
        print('Failed to write SMC history:', e)

    # Append ETF flows snapshot to history
    try:
        etf_val = p.get('metrics', {}).get('etf_flows', {}).get('value')
        hist_etf_path = 'data/history/etf_flows.json'
        os.makedirs(os.path.dirname(hist_etf_path), exist_ok=True)
        if os.path.exists(hist_etf_path):
            with open(hist_etf_path, 'r', encoding='utf-8') as hf:
                etfhist = json.load(hf)
        else:
            etfhist = []
        etfhist.append({
            'timestamp': p['timestamp'],
            'btc_price': p.get('btc_price'),
            'etf_flow_14d': etf_val.get('value') if isinstance(etf_val, dict) else etf_val,
            'etf_flow_daily': etf_val.get('daily_flow') if isinstance(etf_val, dict) else None
        })
        etfhist = etfhist[-730:]
        write_json(hist_etf_path, etfhist)
        print('Wrote', hist_etf_path)
    except Exception as e:
        print('Failed to write ETF flows history:', e)

    # Compute decision-matrix scores and write to data.json
    try:
        from .scoring import compute_scores
        scores = compute_scores(p.get('metrics', {}))
        p['onchain_score'] = scores['onchain_score']
        p['tech_score']    = scores['tech_score']
        p['final_score']   = scores['final_score']
        if scores.get('adaptive'):
            p['adaptive_calibration'] = scores['adaptive']  # transparency: fixed vs blended per metric
        # Invert the index -> Buy/Sell zone prices (not a forecast; see zone_forecast.py)
        try:
            from .zone_forecast import compute_zone_forecast
            zf = compute_zone_forecast(p.get('metrics', {}), p.get('btc_price'))
            if zf:
                p['zone_forecast'] = zf
                print(f"Zone prices: buy={zf['buy']['price']}  sell={zf['sell']['price']}  (realized={zf['realized_price']})")
        except Exception as e:
            print('Failed to compute zone forecast:', e)
        # ── Gemini commentary ──────────────────────────────────────────────────
        import sys
        print('Commentary: starting…', flush=True)
        try:
            from .gemini_commentary import generate_commentary
            from .scoring import build_slider_map
            sm = build_slider_map(p.get('metrics', {}))
            commentary = generate_commentary(p, sm)
            if commentary:
                p['commentary'] = commentary
                print('Commentary: OK', flush=True)
            else:
                print('Commentary: generate_commentary returned None', flush=True)
        except Exception as e:
            print(f'Commentary: exception — {e}', flush=True)
        write_json('data/data.json', p)
        print(f"Scores: onchain={scores['onchain_score']}  tech={scores['tech_score']}  final={scores['final_score']}")
        for mk, mv in (scores.get('adaptive') or {}).items():
            print(f"  adaptive[{mk}]: fixed={mv['fixed']} adaptive={mv.get('adaptive')} -> blended={mv['blended']}")
    except Exception as e:
        print('Failed to compute scores:', e)

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


if __name__ == '__main__':
    main()
