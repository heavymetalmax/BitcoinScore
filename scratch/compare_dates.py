import sys
import datetime
import json
sys.path.insert(0, '.')

from scraper.scoring_v3 import compute_scores_v3
from tools.backtest import _prev_scores, load_data, compute_at

def print_details(date_str, series):
    dt = datetime.date.fromisoformat(date_str)
    r = compute_at(dt, series)
    prev = _prev_scores(dt, series)
    res = compute_scores_v3(r['raw'], target_date=dt, prev_scores=prev)
    
    print(f"\n==================================================")
    print(f"DETAILS FOR {date_str}")
    print(f"Final Score: {res['final_score']}")
    print(f"Phase: {res['phase']} (w_bot: {res['w_bot']:.3f}, w_neutral: {res['w_neutral']:.3f}, w_top: {res['w_top']:.3f})")
    print(f"On-chain Avg: {res['onchain_avg']} | Tech Avg: {res['tech_avg']}")
    print(f"TiZ Score: {res['tiz_score']} | Coherence: {res['oc_coherence']}")
    print(f"==================================================")
    
    print("\nMetric Details:")
    print(f"{'Metric':<22} | {'Raw Value':<12} | {'Normalized':<10} | {'Utility':<10}")
    print("-" * 60)
    
    # We want to print normalized and utility for all normalized metrics
    for k in sorted(res['utilities'].keys()):
        # Let's map normalized key back to raw key to print raw value
        raw_key = k
        if k == 'mvrv_z_score': raw_key = 'mvrv'
        elif k == 'puell': raw_key = 'puell_multiple'
        
        raw_val = r['raw'].get(raw_key)
        # For cipherb, raw is cb
        if k == 'cipherb':
            raw_val = r['raw'].get('cipherb')
            if isinstance(raw_val, dict):
                raw_val = f"W:{raw_val.get('weekly_score')} D:{raw_val.get('daily_score')}"
        
        # Get normalized value from the scoring engine's output
        # Let's run a quick normalization or fetch from a temp run
        # Wait, compute_scores_v3 normalizes them internally. Let's do a quick print.
        # Let's see: to get normalized score we can look up normalized from compute_scores_v3
        # But we didn't return normalized dict. Let's compute it here for display:
        from scraper.normalizer import normalize_metric
        from scraper.scoring_v3 import map_pi_cycle_gap
        
        if k == 'pi_gap':
            pi_raw = r['raw'].get('pi_cycle') or r['raw'].get('pi_cycle_gap_pct') or r['raw'].get('pi_gap')
            norm_val = map_pi_cycle_gap(pi_raw)
        elif k in ('cipherb_weekly', 'cipherb_weekly_fb', 'cipherb_daily'):
            norm_val = None
        elif k == 'cipherb':
            cb = r['raw'].get('cipherb')
            if isinstance(cb, dict):
                w = cb.get('weekly_score')
                d = cb.get('daily_score')
                if w is not None:
                    if cb.get('fast_bearish_div'): w = min(100.0, w + 12)
                    elif cb.get('fast_bullish_div'): w = max(0.0, w - 12)
                    if d is not None:
                        if cb.get('daily_fast_bearish_div'): d = min(100.0, d + 12)
                        elif cb.get('daily_fast_bullish_div'): d = max(0.0, d - 12)
                        norm_val = round(0.8 * w + 0.2 * d)
                    else:
                        norm_val = round(w)
            else:
                norm_val = cb
        else:
            norm_val = normalize_metric(k, r['raw'].get(raw_key), dt)
            
        ut = res['utilities'].get(k, 0.0)
        print(f"{k:<22} | {str(raw_val)[:12]:<12} | {str(norm_val):<10} | {ut:.3f}")

def main():
    series = load_data()
    print_details("2022-11-21", series)
    
    # For today, we can load today's raw from data.json or compute it.
    # Let's see if we can construct today's raw to match what evaluate_today.py does:
    with open('data/data.json', 'r', encoding='utf-8') as f:
        today_data = json.load(f)
    today_raw = {}
    for k, v in today_data.get('metrics', {}).items():
        if isinstance(v, dict) and 'value' in v:
            today_raw[k] = v['value']
        else:
            today_raw[k] = v
    if 'fear_greed' in today_data:
        today_raw['fear_greed'] = today_data['fear_greed']
    if 'm2' in today_data:
        today_raw['m2_yoy'] = today_data['m2']
        
    dt_today = datetime.date.today()
    prev_today = _prev_scores(dt_today, series)
    res_today = compute_scores_v3(today_raw, target_date=dt_today, prev_scores=prev_today)
    
    print(f"\n==================================================")
    print(f"DETAILS FOR TODAY ({dt_today.isoformat()})")
    print(f"Final Score: {res_today['final_score']}")
    print(f"Phase: {res_today['phase']} (w_bot: {res_today['w_bot']:.3f}, w_neutral: {res_today['w_neutral']:.3f}, w_top: {res_today['w_top']:.3f})")
    print(f"On-chain Avg: {res_today['onchain_avg']} | Tech Avg: {res_today['tech_avg']}")
    print(f"TiZ Score: {res_today['tiz_score']} | Coherence: {res_today['oc_coherence']}")
    print(f"==================================================")
    
    print("\nMetric Details:")
    print(f"{'Metric':<22} | {'Raw Value':<12} | {'Normalized':<10} | {'Utility':<10}")
    print("-" * 60)
    for k in sorted(res_today['utilities'].keys()):
        raw_key = k
        if k == 'mvrv_z_score': raw_key = 'mvrv'
        elif k == 'puell': raw_key = 'puell_multiple'
        
        raw_val = today_raw.get(raw_key)
        if k == 'cipherb':
            cb = today_raw.get('cipherb')
            if isinstance(cb, dict):
                raw_val = f"W:{cb.get('weekly_score')} D:{cb.get('daily_score')}"
                
        # Normalize
        from scraper.normalizer import normalize_metric
        from scraper.scoring_v3 import map_pi_cycle_gap
        
        if k == 'pi_gap':
            pi_raw = today_raw.get('pi_cycle') or today_raw.get('pi_cycle_gap_pct') or today_raw.get('pi_gap')
            norm_val = map_pi_cycle_gap(pi_raw)
        elif k in ('cipherb_weekly', 'cipherb_weekly_fb', 'cipherb_daily'):
            norm_val = None
        elif k == 'cipherb':
            cb = today_raw.get('cipherb')
            if isinstance(cb, dict):
                w = cb.get('weekly_score')
                d = cb.get('daily_score')
                if w is not None:
                    if cb.get('fast_bearish_div'): w = min(100.0, w + 12)
                    elif cb.get('fast_bullish_div'): w = max(0.0, w - 12)
                    if d is not None:
                        if cb.get('daily_fast_bearish_div'): d = min(100.0, d + 12)
                        elif cb.get('daily_fast_bullish_div'): d = max(0.0, d - 12)
                        norm_val = round(0.8 * w + 0.2 * d)
                    else:
                        norm_val = round(w)
            else:
                norm_val = cb
        else:
            norm_val = normalize_metric(k, today_raw.get(raw_key), dt_today)
            
        ut = res_today['utilities'].get(k, 0.0)
        print(f"{k:<22} | {str(raw_val)[:12]:<12} | {str(norm_val):<10} | {ut:.3f}")

if __name__ == '__main__':
    main()
