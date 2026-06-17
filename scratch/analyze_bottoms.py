import sys
import datetime
import json
sys.path.insert(0, '.')

from scraper.scoring_v3 import compute_scores_v3
from scraper.orchestrator import orchestrate
from scraper.wave_resonance import compute_wave_resonance
from tools.backtest import _prev_scores, load_data, compute_at

def analyze_date(date_str, label, price, series):
    td = datetime.date.fromisoformat(date_str)
    
    # 1. Compute V3.1ML raw score
    r = compute_at(td, series)
    prev = _prev_scores(td, series)
    v3_res = compute_scores_v3(r['raw'], target_date=td, prev_scores=prev)
    v3_s = v3_res.get('final_score')

    # 2. Wave Resonance for that date
    wr = compute_wave_resonance(date_str)

    # 3. Orchestrator
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

    print(f"{date_str:<12} {label:<22} ${price:>8,} | Raw: {v3_s:2d} | Meta: {orch.get('meta_score'):2d} | Conv: {orch.get('conviction'):.2f} | Zone: {orch.get('zone'):<7} | Flag: {orch.get('flag')}")

def main():
    series = load_data()
    bottoms = [
        ("2018-12-15", "2018 cycle bottom", 3_200),
        ("2020-03-13", "COVID crash", 3_800),
        ("2022-06-18", "Capitulation", 17_600),
        ("2022-11-21", "FTX bottom", 15_500),
    ]
    print("\n" + "="*110)
    print("HISTORICAL BOTTOMS ANALYSIS (V3.1ML)")
    print("="*110)
    for d, l, p in bottoms:
        analyze_date(d, l, p, series)
    print("="*110)

if __name__ == '__main__':
    main()
