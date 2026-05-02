#!/usr/bin/env python3
"""Main scraper skeleton: collects metrics and writes data/data.json

This is a minimal implementation that calls API wrappers and placeholder
MacroMicro functions. Complete Playwright scraping and OCR fallback later.
"""
import datetime
import os
import json
import time
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed yet; run: pip install python-dotenv
from scraper.coingecko import get_price
from scraper.alternative_me import get_fear_greed
from scraper import cmc as cmc_mod
from scraper.nupl import get_nupl
from scraper.mvrv import get_mvrv
from scraper.sopr import get_sopr
from scraper.addresses_in_loss import get_addresses_in_loss
from scraper.m2_metric import get_m2
from scraper.real_yield_metric import get_real_yield
from scraper.geopolitical_risk import get_geopolitical_risk_change
from scraper.utils import human_visit
from scraper.cipherb import get_cipherb
from scraper.smc import get_smc
from scraper.utils import write_json, validate_data


def now_iso():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'


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
                fg = {'value': cmc_fg, 'label': 'CMC'}
                print(f'CMC F&G: {cmc_fg:.0f}')
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
    profit = None
    prev_metrics = None
    cache_path = 'data/data.json'
    try:
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as hf:
                prev = json.load(hf)
                prev_ts = prev.get('timestamp')
                if prev_ts:
                    prev_dt = datetime.datetime.fromisoformat(prev_ts.replace('Z', '+00:00'))
                    age = (datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc) - prev_dt).total_seconds()
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
                    if 'sopr' in prev_metrics and prev_metrics['sopr'].get('value') is not None:
                        profit = prev_metrics['sopr'].get('value')
    except Exception:
        nupl = None
        mvrv = None
        profit = None

    # If not reused from cache, perform live fetches sequentially per metric.
    import random
    from scraper import nupl as nupl_mod
    from scraper import mvrv as mvrv_mod
    from scraper import sopr as sopr_mod
    from scraper import addresses_in_loss as addr_mod
    from scraper import m2_metric as m2_mod
    from scraper import real_yield_metric as ry_mod
    from scraper import geopolitical_risk as gr_mod
    from scraper import cvdd as cvdd_mod
    from scraper import rhodl as rhodl_mod
    from scraper import rainbow as rainbow_mod
    from scraper.utils import is_valid_metric

    metric_specs = [
        ('nupl', nupl_mod.get_nupl, None, lambda r: r),
        ('mvrv', mvrv_mod.get_mvrv, None, lambda r: r),
        ('sopr', sopr_mod.get_sopr, None, lambda r: r),
        ('addresses_in_loss', addr_mod.get_addresses_in_loss, None, lambda r: r),
        ('m2', m2_mod.get_m2, None, lambda r: r),
        ('real_yield', ry_mod.get_real_yield, None, lambda r: r),
        ('geopolitical_risk', gr_mod.get_geopolitical_risk_change, None, lambda r: (r[0] if isinstance(r, (list, tuple)) and len(r) > 0 else (r.get('current') if isinstance(r, dict) else None))),
        ('cvdd_ratio', cvdd_mod.get_cvdd_ratio, None, lambda r: r),
        ('rhodl_ratio', rhodl_mod.get_rhodl_ratio, None, lambda r: r),
    ]

    for name, fn, visit_url, extractor in metric_specs:
        # prefer cached value when available and recent
        cached_val = None
        if prev_metrics and name in prev_metrics and prev_metrics[name].get('value') is not None and 'age' in locals() and age is not None and age <= 86400:
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

            # short randomized pause between scrapers
            time.sleep(1 + random.random() * 2)

        # assign to payload fields where relevant
        if name == 'nupl':
            nupl = val
        if name == 'mvrv':
            mvrv = val
        if name == 'sopr':
            profit = val
        if name == 'addresses_in_loss':
            addresses_in_loss = val
        if name == 'm2':
            m2 = val
        if name == 'real_yield':
            real_yield = val
        if name == 'geopolitical_risk':
            georisk = {'current': val, 'prev': None, 'delta': val} if val is not None else None
        if name == 'cvdd_ratio':
            cvdd_ratio = val
        if name == 'rhodl_ratio':
            rhodl_ratio = val

        # save into metrics dict placeholder (later merged into payload)
        if 'metrics' not in locals():
            metrics = {}
        metrics[name] = {'value': val, 'source': 'BMP' if val is not None else None, 'updated': now_iso()}

    # Rainbow band — separate call (returns dict, not scalar)
    rainbow_band = None
    try:
        rainbow_band = rainbow_mod.get_rainbow_band()
    except Exception as e:
        logger.warning('rainbow band failed: %s', e)
    if 'metrics' not in locals():
        metrics = {}
    metrics['rainbow_band'] = {'value': rainbow_band, 'source': 'BMP', 'updated': now_iso()}
    

    # addresses_in_profit (derived)
    addresses_in_profit = None
    if addresses_in_loss is not None:
        addresses_in_profit = max(0.0, 100.0 - addresses_in_loss)
    # SOPR value from metrics['sopr']
    sopr_value = profit  # `profit` holds the cached/live get_sopr() result
    payload = {
        'timestamp': now_iso(),
        'btc_price': price['price'] if price else None,
        'btc_dominance': btc_dominance,
        'fear_greed': fg['value'] if fg else None,
        'fear_greed_label': fg['label'] if fg else None,
        'nupl': nupl,
        'mvrv_z_score': mvrv,
        'sopr': sopr_value,
        'addresses_in_loss': addresses_in_loss,
        'addresses_in_profit': addresses_in_profit,
        'cvdd_ratio': cvdd_ratio if 'cvdd_ratio' in locals() else None,
        'rhodl_ratio': rhodl_ratio if 'rhodl_ratio' in locals() else None,
        'rainbow_band': rainbow_band if 'rainbow_band' in locals() else None,
        'm2': m2,
        'metrics': {
            # merge our collected metrics dict with static ones
            **(metrics if 'metrics' in locals() else {}),
            'fear_greed': {'value': fg['value'] if fg else None, 'label': fg['label'] if fg else None, 'source': 'Alternative.me', 'updated': now_iso()},
            'addresses_in_profit': {'value': addresses_in_profit, 'source': 'Derived', 'updated': now_iso()},
            'cipherb': {'value': None, 'source': 'Local', 'updated': now_iso()},
            'smc': {'value': None, 'source': 'Local', 'updated': now_iso()}
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
    if p.get('sopr') is not None:
        # SOPR (adjusted) expected in range -0.2..0.5
        sr = p['sopr']
        if sr < -0.5 or sr > 1.0:
            print('Warning: sopr looks invalid, setting to null:', sr)
            p['sopr'] = None

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

    # Populate SMC metric (Price vs Support)
    try:
        smc = get_smc('BTCUSDT', timeframe='1w', size=10)
        if smc and smc.get('last'):
            p['metrics']['smc'] = {'value': smc.get('last'), 'source': 'Local', 'updated': now_iso()}
            write_json('data/data.json', p)
            print('Updated data/data.json with smc')
    except Exception as e:
        print('smc error', e)

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
        from scraper.m2_metric import get_m2 as _get_m2_yoy
        mom_val = _get_m2_yoy()
        if 'metrics' not in p:
            p['metrics'] = {}
        p['metrics']['m2_mom'] = {'value': mom_val, 'source': 'Global M2 YoY (BMP)', 'updated': now_iso()}
        p['m2_mom'] = mom_val
        write_json('data/data.json', p)
        print('Updated data/data.json with M2 MoM')
    except Exception as e:
        print('Failed to compute/include M2 MoM:', e)

    # Append Real Yield snapshot to history for analysis
    try:
        ry_val = p.get('metrics', {}).get('real_yield', {}).get('value')
        hist_ry_path = 'data/history/real_yield.json'
        os.makedirs(os.path.dirname(hist_ry_path), exist_ok=True)
        if os.path.exists(hist_ry_path):
            with open(hist_ry_path, 'r', encoding='utf-8') as hf:
                ryhist = json.load(hf)
        else:
            ryhist = []
        ryhist.append({'timestamp': p['timestamp'], 'btc_price': p.get('btc_price'), 'real_yield': ry_val})
        ryhist = ryhist[-730:]
        write_json(hist_ry_path, ryhist)
        print('Wrote', hist_ry_path)
    except Exception as e:
        print('Failed to write Real Yield history:', e)

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

    # Compute decision-matrix scores and write to data.json
    try:
        from scraper.scoring import compute_scores
        scores = compute_scores(p.get('metrics', {}))
        p['onchain_score'] = scores['onchain_score']
        p['tech_score']    = scores['tech_score']
        p['final_score']   = scores['final_score']
        write_json('data/data.json', p)
        print(f"Scores: onchain={scores['onchain_score']}  tech={scores['tech_score']}  final={scores['final_score']}")
    except Exception as e:
        print('Failed to compute scores:', e)


if __name__ == '__main__':
    main()
