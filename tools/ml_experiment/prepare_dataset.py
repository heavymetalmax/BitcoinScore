#!/usr/bin/env python3
"""Merge all available history files into a unified daily dataset for ML experiment.

Output: tools/ml_experiment/ml_dataset.json
Fields per day:
  date, btc_price, nupl, mvrv, rhodl, puell, asopr,
  cipherb_weekly, cipherb_daily, mayer, pi_cycle_gap,
  global_m2 (lagged -90d), cvdd_ratio

Coverage: 2017-08-17 → latest available
"""
import sys, os, json, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

HIST = 'data/history'
OUT  = os.path.join(os.path.dirname(__file__), 'ml_dataset.json')


def load_series(path, val_key=None):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        series = d.get('series', d) if isinstance(d, dict) else d
        out = {}
        for r in series:
            if isinstance(r, dict):
                date = r.get('date', '')[:10]
                val  = r.get(val_key) if val_key else r.get('value', r.get('close'))
            elif isinstance(r, list) and len(r) >= 2:
                date = str(r[0])[:10]
                val  = r[1]
            else:
                continue
            if date and val is not None:
                out[date] = val
        return out
    except Exception as e:
        print(f'  warn: {path}: {e}')
        return {}


def load_cipherb_weekly():
    try:
        with open(f'{HIST}/cipherb_btcusdt_1w.json', encoding='utf-8') as f:
            raw = json.load(f)
        result = {}
        candles = raw['series']
        for i, c in enumerate(candles):
            week_start = datetime.date.fromtimestamp(c['timestamp'])
            week_end = (datetime.date.fromtimestamp(candles[i+1]['timestamp'])
                        if i + 1 < len(candles) else week_start + datetime.timedelta(days=7))
            score = c.get('weekly_score')
            if score is None:
                continue
            d = week_start
            while d < week_end:
                result[d.isoformat()] = round(score, 2)
                d += datetime.timedelta(days=1)
        return result
    except Exception as e:
        print(f'  warn: cipherb weekly: {e}')
        return {}


def load_cipherb_daily():
    try:
        with open(f'{HIST}/cipherb_btcusdt_1d.json', encoding='utf-8') as f:
            raw = json.load(f)
        return {r['date']: round(r['daily_score'], 2) for r in raw['series'] if r.get('daily_score') is not None}
    except Exception as e:
        print(f'  warn: cipherb daily: {e}')
        return {}


def load_pi_cycle():
    try:
        with open(f'{HIST}/pi_cycle_daily.json', encoding='utf-8') as f:
            raw = json.load(f)
        return {r['date']: r['gap_pct'] for r in raw['series'] if r.get('gap_pct') is not None}
    except Exception as e:
        print(f'  warn: pi_cycle: {e}')
        return {}


def load_mayer():
    try:
        with open(f'{HIST}/mayer_daily.json', encoding='utf-8') as f:
            raw = json.load(f)
        return {r['date']: r['mayer_multiple'] for r in raw['series'] if r.get('mayer_multiple') is not None}
    except Exception as e:
        print(f'  warn: mayer: {e}')
        return {}


def load_global_m2_lagged(lag_days=90):
    """Global M2 lagged by 90 days (BTC tends to lead M2 by ~3 months)."""
    try:
        with open(f'{HIST}/global_m2_history.json', encoding='utf-8') as f:
            raw = json.load(f)
        series = raw.get('series', [])
        # series is list of {date, value} or [[date, value]]
        m2_map = {}
        for r in series:
            if isinstance(r, dict):
                m2_map[r['date'][:10]] = r.get('value')
            elif isinstance(r, list):
                m2_map[str(r[0])[:10]] = r[1]

        lagged = {}
        for date_str, val in m2_map.items():
            if val is None:
                continue
            future = (datetime.date.fromisoformat(date_str) + datetime.timedelta(days=lag_days)).isoformat()
            lagged[future] = round(val, 4)
        return lagged
    except Exception as e:
        print(f'  warn: global_m2: {e}')
        return {}


def nearest(data, date_str, window=7):
    if date_str in data:
        return data[date_str]
    target = datetime.date.fromisoformat(date_str)
    best_v, best_dist = None, window + 1
    for k, v in data.items():
        try:
            dist = abs((datetime.date.fromisoformat(k) - target).days)
        except ValueError:
            continue
        if dist < best_dist:
            best_dist, best_v = dist, v
    return best_v


def main():
    print('Loading history files…')
    price_h   = load_series(f'{HIST}/btc_price_history.json', 'close')
    nupl_h    = load_series(f'{HIST}/nupl_history.json')
    mvrv_h    = load_series(f'{HIST}/mvrv_history.json')
    rhodl_h   = load_series(f'{HIST}/rhodl_history.json')
    puell_h   = load_series(f'{HIST}/puell_history.json')
    asopr_h   = load_series(f'{HIST}/asopr_history.json')
    cvdd_h    = load_series(f'{HIST}/cvdd_ratio_history.json', 'ratio')
    cb_w_h    = load_cipherb_weekly()
    cb_d_h    = load_cipherb_daily()
    mayer_h   = load_mayer()
    pi_h      = load_pi_cycle()
    m2_h      = load_global_m2_lagged(90)

    print(f'  price={len(price_h)} nupl={len(nupl_h)} mvrv={len(mvrv_h)} '
          f'rhodl={len(rhodl_h)} puell={len(puell_h)} asopr={len(asopr_h)}')
    print(f'  cvdd={len(cvdd_h)} cb_w={len(cb_w_h)} cb_d={len(cb_d_h)} '
          f'mayer={len(mayer_h)} pi={len(pi_h)} m2={len(m2_h)}')

    # Date range: first BTC price → today
    dates = sorted(price_h.keys())
    print(f'  date range: {dates[0]} → {dates[-1]} ({len(dates)} days)')

    rows = []
    for date_str in dates:
        price = price_h.get(date_str)
        if price is None:
            continue
        row = {
            'date':           date_str,
            'btc_price':      price,
            'nupl':           nearest(nupl_h,  date_str),
            'mvrv':           nearest(mvrv_h,  date_str),
            'rhodl':          nearest(rhodl_h, date_str),
            'puell':          nearest(puell_h, date_str),
            'asopr':          nearest(asopr_h, date_str),
            'cvdd_ratio':     nearest(cvdd_h,  date_str),
            'cipherb_weekly': cb_w_h.get(date_str),
            'cipherb_daily':  cb_d_h.get(date_str),
            'mayer':          mayer_h.get(date_str),
            'pi_cycle_gap':   pi_h.get(date_str),
            'global_m2':      nearest(m2_h,    date_str, window=14),
        }
        rows.append(row)

    # Coverage stats
    def cov(key):
        n = sum(1 for r in rows if r.get(key) is not None)
        return f'{key}:{n}/{len(rows)}'

    print('\nCoverage:', ' '.join(cov(k) for k in
          ['nupl','mvrv','rhodl','puell','asopr','cvdd_ratio',
           'cipherb_weekly','cipherb_daily','mayer','pi_cycle_gap','global_m2']))

    out = {
        'meta': {
            'generated':   datetime.datetime.utcnow().isoformat() + 'Z',
            'n':           len(rows),
            'date_from':   rows[0]['date'],
            'date_to':     rows[-1]['date'],
            'fields':      list(rows[0].keys()),
            'notes': [
                'global_m2: lagged +90 days (BTC leads M2 by ~3 months)',
                'cipherb_weekly: same value for all days within the week',
                'pi_cycle_gap: % gap between 111DMA and 350DMA×2 (negative = cross = top signal)',
                'mayer: price / 200DMA',
                'nupl range: -0.5 (bottom) to 0.75 (top)',
                'mvrv range: -1 (bottom) to +7 (top)',
                'rhodl range: 800 (bottom) to 50000+ (top)',
                'puell range: 0.3 (bottom) to 5+ (top)',
                'asopr range: 0.95 (bottom) to 1.10 (top)',
                'cvdd_ratio: btc_price/cvdd_price — <1.5 bottom, >5 top',
            ],
        },
        'series': rows,
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f)
    print(f'\nWrote {OUT}  ({len(rows)} days)')
    return rows


if __name__ == '__main__':
    main()
