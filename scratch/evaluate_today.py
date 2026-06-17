import sys
import datetime
import json
sys.path.insert(0, '.')

from scraper.scoring_v3 import compute_scores_v3
from scraper.orchestrator import orchestrate
from scraper.wave_resonance import compute_wave_resonance
from tools.backtest import _prev_scores, load_data

def main():
    print("Loading today's scraped metrics from data.json...")
    with open('data/data.json', 'r', encoding='utf-8') as f:
        today_data = json.load(f)

    # Reconstruct raw metrics dictionary from today_data['metrics']
    raw_metrics = {}
    for k, v in today_data.get('metrics', {}).items():
        if isinstance(v, dict) and 'value' in v:
            # Handle special structures (like cipherb or etf_flows)
            raw_metrics[k] = v['value']
        else:
            raw_metrics[k] = v

    # Inject fear_greed and other fields from top level if needed
    if 'fear_greed' in today_data:
        raw_metrics['fear_greed'] = today_data['fear_greed']
    if 'm2' in today_data:
        raw_metrics['m2_yoy'] = today_data['m2']

    # Load history for lookback scores
    series = load_data()
    today_dt = datetime.date.today()
    prev = _prev_scores(today_dt, series)

    # Compute V3.1ML Score
    v3_res = compute_scores_v3(raw_metrics, target_date=today_dt, prev_scores=prev)
    v3_s = v3_res.get('final_score')

    print(f"\n==================================================")
    print(f"TODAY'S SCORING ANALYSIS (Date: {today_dt.isoformat()})")
    print(f"Price: ${today_data.get('btc_price', 0.0):,.2f}")
    print(f"==================================================")

    print("\n--- Phase Classifier Probabilities (V3.1ML) ---")
    print(f"  BOTTOM Phase Weight (w_bot)     : {v3_res.get('w_bot'):.3f} ({round(v3_res.get('w_bot')*100)}%)")
    print(f"  NEUTRAL Phase Weight (w_neutral): {v3_res.get('w_neutral'):.3f} ({round(v3_res.get('w_neutral')*100)}%)")
    print(f"  TOP Phase Weight (w_top)        : {v3_res.get('w_top'):.3f} ({round(v3_res.get('w_top')*100)}%)")
    print(f"  Classified Phase                : {v3_res.get('phase')}")

    print("\n--- Metric Utilities in BOTTOM Phase ---")
    for k, v in v3_res.get('utilities', {}).items():
        print(f"  {k:<22} : {v:.3f}")

    print("\n--- Final Raw Scores ---")
    print(f"  V3.1ML Raw Score                : {v3_s}")
    print(f"  V3.1ML On-Chain Avg             : {v3_res.get('onchain_avg')}")
    print(f"  V3.1ML Tech Avg                 : {v3_res.get('tech_avg')}")

    # Wave Resonance for today
    mayer_val = raw_metrics.get('mayer_multiple')
    if isinstance(mayer_val, dict):
        mayer_val = mayer_val.get('value')
    pi_val = raw_metrics.get('pi_cycle')
    pi_gap_live = pi_val.get('gap_pct') if isinstance(pi_val, dict) else None
    
    wr_live = {
        'nupl':           raw_metrics.get('nupl'),
        'mvrv':           raw_metrics.get('mvrv'),
        'puell':          raw_metrics.get('puell_multiple') or raw_metrics.get('puell'),
        'cvdd_ratio':     raw_metrics.get('cvdd_ratio'),
        'mayer_multiple': mayer_val,
        'pi_gap_pct':     pi_gap_live,
    }
    wr = compute_wave_resonance(raw_overrides={k: v for k, v in wr_live.items() if v is not None})
    print(f"\n--- Wave Resonance Today ---")
    print(f"  Score                           : {wr.get('score')}")
    print(f"  Coherence                       : {wr.get('coherence')}")

    # Orchestrator
    orch = orchestrate(
        v2_score=v3_s,
        v2_oc_coherence=v3_res.get('oc_coherence', 1.0),
        wr_score=wr.get('score'),
        wr_coherence=wr.get('coherence'),
        tiz_maturity=v3_res.get('tiz_maturity'),
        top_signal=v3_res.get('top_signal'),
        bot_signal=v3_res.get('bot_signal'),
        is_v3=True
    )

    print(f"\n--- Orchestrator Meta-Score & Signals ---")
    print(f"  Final Meta-Score (Risk index)   : {orch.get('meta_score')}")
    print(f"  Zone                            : {orch.get('zone').upper()}")
    print(f"  Conviction                      : {orch.get('conviction'):.3f}")
    print(f"  Flag                            : {orch.get('flag')}")
    print(f"  Agreement                       : {orch.get('agreement').upper()}")

if __name__ == '__main__':
    main()
