"""
Build data/history/unified_history.json — one row per date, all metric raw values.

Sources (by metric):
  nupl          nupl_history.json          2010+  [date, value]
  mvrv          mvrv_history.json          2010+  [date, value]  (Z-score)
  puell         puell_history.json         2010+  [date, value]
  rhodl_ratio   rhodl_history.json         2010+  [date, value]
  asopr         asopr_history.json         2016+  [date, value]
  cvdd_ratio    cvdd_ratio_history.json    2017+  {date, ratio, btc_price}
  mayer_multiple mayer_daily.json          2018+  {date, mayer_multiple}
                 mayer_history.json        2014+  [date, value]  (fills pre-2018 gap)
  fear_greed    fear_greed.json            2018+  {date, score}
  m2_yoy        m2_yoy_history.json         2010+  direct YoY % (digitized from MacroMicro)
                global_m2_history.json     2014+  fallback: computed YoY % from absolute values
  cipherb_daily cipherb_btcusdt_1d.json    2017+  {date, daily_score}
  pi_gap_pct    pi_cycle_daily.json        2018+  {date, gap_pct, cross}
  dxy           dxy_history.json           2020+  {date, value}
  funding_rate  funding_rate_history.json  2024+  {date, value}  (written by history_writer)
  btc_price     btc_price_history.json     2017+  [date, value] or {date, value/close}

Run: python tools/build_unified_history.py
"""
import json
import os
import datetime
from collections import defaultdict

BASE = 'data/history'
OUT  = f'{BASE}/unified_history.json'


def load_list_series(filename):
    """Load [[date, value], ...] → {date: value}"""
    path = os.path.join(BASE, filename)
    if not os.path.exists(path):
        return {}
    data = json.load(open(path, encoding='utf-8'))
    series = data.get('series', data) if isinstance(data, dict) else data
    result = {}
    for r in series:
        if isinstance(r, list) and len(r) >= 2:
            d, v = str(r[0])[:10], r[1]
            if d and v is not None:
                result[d] = float(v)
    return result


def load_dict_series(filename, value_key, date_key='date'):
    """Load [{date: ..., value_key: ...}, ...] → {date: value}"""
    path = os.path.join(BASE, filename)
    if not os.path.exists(path):
        return {}
    data = json.load(open(path, encoding='utf-8'))
    series = data.get('series', data) if isinstance(data, dict) else data
    result = {}
    for r in series:
        if isinstance(r, dict):
            d = str(r.get(date_key, ''))[:10]
            v = r.get(value_key)
            if d and v is not None:
                result[d] = float(v)
    return result


def compute_m2_yoy(filename, window_days=365):
    """Compute YoY % from absolute M2 values."""
    path = os.path.join(BASE, filename)
    if not os.path.exists(path):
        return {}
    data = json.load(open(path, encoding='utf-8'))
    series = data.get('series', data) if isinstance(data, dict) else data
    idx = {}
    for r in series:
        if isinstance(r, dict):
            d, v = str(r.get('date', ''))[:10], r.get('value')
        elif isinstance(r, list) and len(r) >= 2:
            d, v = str(r[0])[:10], r[1]
        else:
            continue
        if d and v is not None:
            idx[d] = float(v)
    dates = sorted(idx.keys())
    result = {}
    for d in dates:
        try:
            prev = (datetime.date.fromisoformat(d) - datetime.timedelta(days=window_days)).isoformat()
        except ValueError:
            continue
        # find nearest past date within ±10 days
        v_now = idx.get(d)
        v_prev = None
        for offset in range(0, 11):
            cand = (datetime.date.fromisoformat(prev) + datetime.timedelta(days=offset)).isoformat()
            if cand in idx:
                v_prev = idx[cand]
                break
        if v_now and v_prev and v_prev != 0:
            result[d] = round((v_now / v_prev - 1) * 100, 4)
    return result


def load_btc_price(filename):
    """Load BTC price — handles both list and dict formats."""
    path = os.path.join(BASE, filename)
    if not os.path.exists(path):
        return {}
    data = json.load(open(path, encoding='utf-8'))
    series = data.get('series', data) if isinstance(data, dict) else data
    result = {}
    for r in series:
        if isinstance(r, list) and len(r) >= 2:
            d, v = str(r[0])[:10], r[1]
        elif isinstance(r, dict):
            d = str(r.get('date', ''))[:10]
            v = r.get('close') or r.get('value') or r.get('price')
        else:
            continue
        if d and v is not None:
            result[d] = float(v)
    return result


def main():
    print("Loading sources...")

    nupl     = load_list_series('nupl_history.json')
    mvrv     = load_list_series('mvrv_history.json')
    puell    = load_list_series('puell_history.json')
    rhodl    = load_list_series('rhodl_history.json')
    asopr    = load_list_series('asopr_history.json')
    cvdd     = load_dict_series('cvdd_ratio_history.json', 'ratio')
    mayer_d  = load_dict_series('mayer_daily.json', 'mayer_multiple')
    mayer_h  = load_list_series('mayer_history.json')
    fg       = load_dict_series('fear_greed.json', 'score')
    m2_yoy_path = os.path.join(BASE, 'm2_yoy_history.json')
    if os.path.exists(m2_yoy_path):
        raw_m2 = json.load(open(m2_yoy_path, encoding='utf-8'))
        m2_ser = raw_m2.get('series', raw_m2) if isinstance(raw_m2, dict) else raw_m2
        m2_yoy = {str(r.get('date', ''))[:10]: float(r['value'])
                  for r in m2_ser if isinstance(r, dict) and r.get('date') and r.get('value') is not None}
    else:
        m2_yoy = compute_m2_yoy('global_m2_history.json')
    cb_d     = load_dict_series('cipherb_btcusdt_1d.json', 'daily_score')
    pi       = load_dict_series('pi_cycle_daily.json', 'gap_pct')
    dxy      = load_dict_series('dxy_history.json', 'value')
    # dxy_scraper_history.json written daily by history_writer — merge over FRED data
    dxy.update(load_dict_series('dxy_scraper_history.json', 'value'))
    fr       = load_dict_series('funding_rate_history.json', 'value')
    btc      = load_btc_price('btc_price_history.json')

    # mayer: prefer mayer_daily (more precise), fill pre-2018 from mayer_history
    mayer = {**mayer_h, **mayer_d}

    all_dates = sorted(set(
        list(nupl) + list(mvrv) + list(puell) + list(rhodl) +
        list(asopr) + list(cvdd) + list(mayer) + list(fg) +
        list(m2_yoy) + list(cb_d) + list(pi) + list(dxy) + list(fr) + list(btc)
    ))

    print(f"  nupl:          {len(nupl):5d} pts  ({min(nupl) if nupl else '?'} → {max(nupl) if nupl else '?'})")
    print(f"  mvrv:          {len(mvrv):5d} pts")
    print(f"  puell:         {len(puell):5d} pts")
    print(f"  rhodl_ratio:   {len(rhodl):5d} pts")
    print(f"  asopr:         {len(asopr):5d} pts")
    print(f"  cvdd_ratio:    {len(cvdd):5d} pts")
    print(f"  mayer_multiple:{len(mayer):5d} pts  ({min(mayer) if mayer else '?'} → {max(mayer) if mayer else '?'})")
    print(f"  fear_greed:    {len(fg):5d} pts")
    print(f"  m2_yoy:        {len(m2_yoy):5d} pts  ({min(m2_yoy) if m2_yoy else '?'} → {max(m2_yoy) if m2_yoy else '?'})")
    print(f"  cipherb_daily: {len(cb_d):5d} pts")
    print(f"  pi_gap_pct:    {len(pi):5d} pts")
    print(f"  dxy:           {len(dxy):5d} pts  ({min(dxy) if dxy else '?'} → {max(dxy) if dxy else '?'})")
    print(f"  funding_rate:  {len(fr):5d} pts  ({min(fr) if fr else 'no history yet'})")
    print(f"  btc_price:     {len(btc):5d} pts")
    print(f"\nTotal unique dates: {len(all_dates)}  ({all_dates[0]} → {all_dates[-1]})")

    series = []
    for d in all_dates:
        row = {'date': d}
        row['nupl']           = nupl.get(d)
        row['mvrv']           = mvrv.get(d)
        row['puell']          = puell.get(d)
        row['rhodl_ratio']    = rhodl.get(d)
        row['asopr']          = asopr.get(d)
        row['cvdd_ratio']     = cvdd.get(d)
        row['mayer_multiple'] = mayer.get(d)
        row['fear_greed']     = fg.get(d)
        row['m2_yoy']         = m2_yoy.get(d)
        row['cipherb_daily']  = cb_d.get(d)
        row['pi_gap_pct']     = pi.get(d)
        row['dxy']            = dxy.get(d)
        row['funding_rate']   = fr.get(d)
        row['btc_price']      = btc.get(d)
        series.append(row)

    # Coverage stats
    print("\nCoverage per metric (% of dates with non-null value):")
    for col in ['nupl','mvrv','puell','rhodl_ratio','asopr','cvdd_ratio',
                'mayer_multiple','fear_greed','m2_yoy','cipherb_daily','pi_gap_pct',
                'dxy','funding_rate','btc_price']:
        n = sum(1 for r in series if r.get(col) is not None)
        pct = n / len(series) * 100
        first = next((r['date'] for r in series if r.get(col) is not None), '?')
        print(f"  {col:20s}  {n:5d}/{len(series)}  ({pct:5.1f}%)  from {first}")

    out = {
        'meta': {
            'generated': datetime.datetime.utcnow().isoformat() + 'Z',
            'note': (
                'Unified raw metric history. On-chain (nupl/mvrv/puell/rhodl) from 2010; '
                'cvdd_ratio/mayer/cipherb from 2017-2018; fear_greed from 2018; '
                'm2_yoy (digitized from MacroMicro image) from 2010.'
            ),
            'periods': {
                'on_chain_full': '2010+  (nupl, mvrv, puell, rhodl_ratio)',
                'on_chain_partial': '2016+  (asopr),  2017+  (cvdd_ratio)',
                'tech': '2017-2018+  (mayer, cipherb, pi_gap_pct)',
                'macro': '2010+  (m2_yoy),  2018+  (fear_greed),  2020+  (dxy),  2024+  (funding_rate)',
            },
        },
        'series': series,
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'))

    size_kb = os.path.getsize(OUT) / 1024
    print(f"\nWrote {OUT}  ({size_kb:.0f} KB,  {len(series)} rows)")


if __name__ == '__main__':
    main()
