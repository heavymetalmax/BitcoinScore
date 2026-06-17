import sys
import datetime
import json
sys.path.insert(0, '.')

from tools.backtest import load_data, compute_at, v2_at, v3_at
from scraper.wave_resonance import compute_wave_resonance
from scraper.orchestrator import orchestrate
from scraper.scoring_v2 import phase_signals, _METRIC_LOOKBACK

def analyze_date(date_str, label, price, series):
    print(f"\n==================================================")
    print(f"Analyzing Date: {date_str} - {label} (${price:,})")
    print(f"==================================================")
    
    td = datetime.date.fromisoformat(date_str)
    
    # Compute base metrics
    r = compute_at(td, series)
    sc = r['scores']
    
    print("\n--- Individual Risk Scores (0-100) ---")
    for k, v in sc.items():
        print(f"  {k:<20}: {v}")
        
    # Get v2 scores
    diag_s, full_s = v2_at(td, series)
    print(f"\n--- V2 Scores ---")
    print(f"  Diagonal Covariance (diag) : {diag_s}")
    print(f"  Full Covariance (full)     : {full_s}")
    
    # Get v3 score
    v3_res = v3_at(td, series)
    v3_s = v3_res.get('final_score')
    print(f"\n--- V3 Score ---")
    print(f"  V3 Score                   : {v3_s}")
    print(f"  V3 Details:")
    print(f"    w_top                    : {v3_res.get('w_top')}")
    print(f"    w_bot                    : {v3_res.get('w_bot')}")
    print(f"    w_neutral                : {v3_res.get('w_neutral')}")
    print(f"    phase                    : {v3_res.get('phase')}")
    print(f"    top_signal               : {v3_res.get('top_signal')}%")
    print(f"    bot_signal               : {v3_res.get('bot_signal')}%")
    print(f"    oc_coherence             : {v3_res.get('oc_coherence')}")
    print(f"    coh_factor               : {v3_res.get('coh_factor')}")
    print(f"    pi_cross                 : {v3_res.get('pi_cross')}")
    
    # Wave Resonance
    wr = compute_wave_resonance(date_str)
    print(f"\n--- Wave Resonance ---")
    print(f"  Score                      : {wr.get('score')}")
    print(f"  Coherence                  : {wr.get('coherence')}")
    print(f"  Mean Oscillator            : {wr.get('mean_osc')}")
    print(f"  Oscillators:")
    for k, v in wr.get('oscillators', {}).items():
        print(f"    {k:<20}: {v}")

    # Phase Signals from V2
    from tools.backtest import _prev_scores
    prev = _prev_scores(td, series)
    ps_v2_diag = phase_signals(r['scores'], prev, use_full_mahalanobis=False)
    ps_v2_full = phase_signals(r['scores'], prev, use_full_mahalanobis=True)
    print(f"\n--- Phase Signals (Mahalanobis Proximities) ---")
    print(f"  Diag Mahalanobis:")
    print(f"    top_signal               : {ps_v2_diag.get('top_signal')}%")
    print(f"    bot_signal               : {ps_v2_diag.get('bot_signal')}%")
    print(f"    phase                    : {ps_v2_diag.get('phase')}")
    print(f"  Full Mahalanobis:")
    print(f"    top_signal               : {ps_v2_full.get('top_signal')}%")
    print(f"    bot_signal               : {ps_v2_full.get('bot_signal')}%")
    print(f"    phase                    : {ps_v2_full.get('phase')}")

    # Orchestration using V2 Full (which is the default or Fisher in live)
    try:
        from scraper.scoring import compute_scores_v2_fisher
        fisher_out = compute_scores_v2_fisher(r['raw'])
        print(f"\n--- Fisher (V1.5) Score ---")
        print(f"  Fisher Score               : {fisher_out.get('final_score')}")
    except Exception as e:
        print(f"  Could not compute Fisher: {e}")
        fisher_out = {}

    print(f"\n--- Orchestrator Outputs (using V3) ---")
    tiz_mat = v3_res.get('tiz_maturity')
    orch_v3 = orchestrate(
        v2_score=v3_s,
        v2_oc_coherence=v3_res.get('oc_coherence', 1.0),
        wr_score=wr.get('score'),
        wr_coherence=wr.get('coherence'),
        tiz_maturity=tiz_mat,
        top_signal=v3_res.get('top_signal'),
        bot_signal=v3_res.get('bot_signal'),
        is_v3=True
    )
    print(json.dumps(orch_v3, indent=2))

    print(f"\n--- Orchestrator Outputs (using V2 Full Mahalanobis / Fisher) ---")
    orch_fisher = orchestrate(
        v2_score=fisher_out.get('final_score', full_s),
        v2_oc_coherence=fisher_out.get('oc_coherence', r.get('oc_coherence', 1.0)),
        wr_score=wr.get('score'),
        wr_coherence=wr.get('coherence'),
        tiz_maturity=None,
        top_signal=ps_v2_diag.get('top_signal'),
        bot_signal=ps_v2_diag.get('bot_signal'),
        is_v3=False
    )
    print(json.dumps(orch_fisher, indent=2))

if __name__ == '__main__':
    # Load once
    series = load_data()
    
    # Analyze critical ATH dates
    dates = [
        ("2021-04-14", "Spring ATH", 63_500),
        ("2021-11-10", "Nov 2021 ATH", 69_000),
        ("2024-03-14", "2024 ATH", 73_500),
        ("2025-09-29", "Intraday ATH", 129_000),
    ]
    for d, l, p in dates:
        analyze_date(d, l, p, series)
