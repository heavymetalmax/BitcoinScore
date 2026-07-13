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
        smc_val = None
        try:
            smc_val = m.get('smc', {}).get('value')
        except Exception:
            pass
        _append('data/history/smc.json',
                {'timestamp': ts, 'btc_price': price, 'smc': smc_val})
        print('Wrote data/history/smc.json')
    except Exception as e:
        print('Failed to write SMC history:', e)

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
