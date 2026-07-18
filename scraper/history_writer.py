"""Append daily metric snapshots to per-metric history files in data/history/."""
import datetime
import json
import os

from .utils import write_json


def _now():
    return (datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0, tzinfo=None).isoformat() + 'Z')


def _append(path, entry, maxlen=730):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    hist = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
    hist.append(entry)
    write_json(path, hist[-maxlen:])


def write_metric_histories(p):
    """Append today's values to all per-metric history files and resolve M2 MoM."""
    ts    = p['timestamp']
    price = p.get('btc_price')
    m     = p.get('metrics', {})

    # ── M2 MoM resolution ────────────────────────────────────────────────────
    # Reads metrics.m2 (already fetched) or falls back to m2_yoy_history.json.
    # Writes m2_mom into the payload and to data/history/m2.json.
    try:
        m2_entry = m.get('m2') or {}
        mom_val  = m2_entry.get('value') if isinstance(m2_entry, dict) else m2_entry
        m2_src   = m2_entry.get('source', 'MacroMicro') if isinstance(m2_entry, dict) else 'MacroMicro'
        if mom_val is None:
            try:
                with open('data/history/m2_yoy_history.json', encoding='utf-8') as fh:
                    raw_h  = json.load(fh)
                    series = raw_h.get('series', raw_h) if isinstance(raw_h, dict) else raw_h
                    for row in reversed(series):
                        v = row.get('value') if isinstance(row, dict) else None
                        if v is not None:
                            mom_val = float(v)
                            m2_src  = f'MacroMicro history ({row.get("date","unknown")})'
                            break
            except Exception:
                pass
        if mom_val is not None:
            p.setdefault('metrics', {})['m2_mom'] = {
                'value': mom_val, 'source': m2_src, 'updated': _now()}
            p['m2_mom'] = mom_val
            write_json('data/data.json', p)
            print(f'Updated data/data.json with M2 MoM = {mom_val} ({m2_src})')
        else:
            print('M2 MoM: no value from metrics.m2 or history fallback; leaving None')
    except Exception as e:
        print('Failed to compute/include M2 MoM:', e)

    # ── Append to per-metric history files ────────────────────────────────────
    try:
        _append('data/history/m2.json',
                {'timestamp': ts, 'btc_price': price, 'm2': p.get('m2')})
        print('Wrote data/history/m2.json')
    except Exception as e:
        print('Failed to write M2 history:', e)

    try:
        yc_val = m.get('yield_curve', {}).get('value')
        _append('data/history/yield_curve.json',
                {'timestamp': ts, 'btc_price': price, 'yield_curve': yc_val})
        print('Wrote data/history/yield_curve.json')
    except Exception as e:
        print('Failed to write Yield Curve history:', e)

    try:
        cb_val = m.get('cipherb', {}).get('value')
        _append('data/history/cipherb.json',
                {'timestamp': ts, 'btc_price': price, 'cipherb': cb_val})
        print('Wrote data/history/cipherb.json')
    except Exception as e:
        print('Failed to write CipherB history:', e)

    try:
        etf_val = m.get('etf_flows', {}).get('value')
        _flow_7d = etf_val.get('value')      if isinstance(etf_val, dict) else etf_val
        _daily   = etf_val.get('daily_flow') if isinstance(etf_val, dict) else None
        _append('data/history/etf_flows.json', {
            'timestamp':      ts,
            'btc_price':      price,
            'etf_flow_7d':    _flow_7d,
            'etf_flow_daily': _daily,
        })
        print('Wrote data/history/etf_flows.json')
    except Exception as e:
        print('Failed to write ETF flows history:', e)

    try:
        fr_raw = m.get('funding_rate', {})
        # metrics.funding_rate.value is a nested dict: {latest, avg_7d, score, ...}
        fr_inner = fr_raw.get('value') if isinstance(fr_raw, dict) else fr_raw
        fr_avg7d = fr_inner.get('avg_7d') if isinstance(fr_inner, dict) else fr_inner
        if fr_avg7d is not None:
            date_str = ts[:10] if ts else datetime.datetime.utcnow().strftime('%Y-%m-%d')
            _append('data/history/funding_rate_history.json', {
                'date': date_str, 'btc_price': price, 'value': float(fr_avg7d)
            })
            print(f'Wrote data/history/funding_rate_history.json  (avg_7d={fr_avg7d})')
    except Exception as e:
        print('Failed to write funding_rate history:', e)

    try:
        dxy_raw = m.get('dxy', {})
        dxy_val = dxy_raw.get('value') if isinstance(dxy_raw, dict) else dxy_raw
        if dxy_val is not None:
            date_str = ts[:10] if ts else datetime.datetime.utcnow().strftime('%Y-%m-%d')
            # dxy_history.json uses {series: [...]} format maintained by dxy_metric.py.
            # Append to flat-list dxy_scraper_history.json to avoid format conflict.
            _append('data/history/dxy_scraper_history.json',
                    {'date': date_str, 'btc_price': price, 'value': float(dxy_val)})
            print(f'Wrote data/history/dxy_scraper_history.json  ({dxy_val})')
    except Exception as e:
        print('Failed to write DXY scraper history:', e)

    # ── Fear & Greed — append daily to fear_greed.json ───────────────────────
    try:
        fg_raw   = m.get('fear_greed', {})
        fg_avg7d = fg_raw.get('avg_7d') if isinstance(fg_raw, dict) else p.get('fear_greed')
        fg_label = fg_raw.get('label', '') if isinstance(fg_raw, dict) else ''
        if fg_avg7d is not None:
            date_str = ts[:10] if ts else datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
            path = 'data/history/fear_greed.json'
            hist = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
            if not hist or hist[-1].get('date') != date_str:
                hist.append({'date': date_str, 'score': round(float(fg_avg7d), 2), 'label': fg_label})
                write_json(path, hist)
                print(f'Wrote data/history/fear_greed.json  (avg_7d={fg_avg7d})')
            else:
                print(f'fear_greed.json already has entry for {date_str}, skipping')
    except Exception as e:
        print('Failed to write Fear & Greed history:', e)

    # ── NUPL — append daily to nupl_history.json ─────────────────────────────
    try:
        nupl_raw = m.get('nupl', {})
        nupl_val = nupl_raw.get('value') if isinstance(nupl_raw, dict) else nupl_raw
        if nupl_val is not None:
            date_str = ts[:10] if ts else datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
            path = 'data/history/nupl_history.json'
            hist = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
            last_date = hist[-1][0] if hist and isinstance(hist[-1], list) else None
            if last_date != date_str:
                hist.append([date_str, float(nupl_val) / 100.0])  # BMP returns %, history stores fraction
                write_json(path, hist)
                print(f'Wrote data/history/nupl_history.json  ({nupl_val})')
            else:
                print(f'nupl_history.json already has entry for {date_str}, skipping')
    except Exception as e:
        print('Failed to write NUPL history:', e)

    # ── aSOPR — append daily to asopr_history.json ───────────────────────────
    try:
        asopr_raw = m.get('asopr', {})
        asopr_val = asopr_raw.get('value') if isinstance(asopr_raw, dict) else asopr_raw
        if asopr_val is not None:
            date_str = ts[:10] if ts else datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
            path = 'data/history/asopr_history.json'
            hist = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
            # asopr_history.json uses [[date, value], ...] format
            last_date = hist[-1][0] if hist and isinstance(hist[-1], list) else None
            if last_date != date_str:
                hist.append([date_str, float(asopr_val)])
                write_json(path, hist)
                print(f'Wrote data/history/asopr_history.json  ({asopr_val})')
            else:
                print(f'asopr_history.json already has entry for {date_str}, skipping')
    except Exception as e:
        print('Failed to write aSOPR history:', e)

    # ── M2 YoY — append monthly to m2_yoy_history.json ──────────────────────
    try:
        m2_raw = m.get('m2', {})
        m2_val = m2_raw.get('value') if isinstance(m2_raw, dict) else m2_raw
        if m2_val is not None:
            date_str = ts[:10] if ts else datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
            month_str = date_str[:7]   # YYYY-MM
            path = 'data/history/m2_yoy_history.json'
            raw_h = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
            hist  = raw_h if isinstance(raw_h, list) else raw_h.get('series', [])
            last_month = hist[-1].get('date', '')[:7] if hist and isinstance(hist[-1], dict) else None
            if last_month != month_str:
                hist.append({'date': date_str, 'value': round(float(m2_val), 4)})
                out = raw_h if isinstance(raw_h, list) else {'series': hist}
                if isinstance(out, dict):
                    out['series'] = hist
                write_json(path, out)
                print(f'Wrote data/history/m2_yoy_history.json  ({m2_val})')
            else:
                print(f'm2_yoy_history.json already has entry for {month_str}, skipping')
    except Exception as e:
        print('Failed to write M2 YoY history:', e)
