"""Full scoring pipeline: V1 → V2 → V3 → Fisher+Orch → V3.2 override.

Call run_scoring_pipeline(p, build_metric_history_fn) from scraper.main().
Modifies p in place, writes data.json / data_exp.json, returns p.
"""
import copy
import os

from .utils import write_json


def run_scoring_pipeline(p, build_metric_history_fn=None):
    """Run all scoring passes on payload p. Returns updated p."""

    # ── V1 scores ─────────────────────────────────────────────────────────────
    try:
        from .scoring import compute_scores, build_slider_map
        from .zone_forecast import compute_zone_forecast
        scores = compute_scores(p.get('metrics', {}))
        p['onchain_score'] = scores['onchain_score']
        p['tech_score']    = scores['tech_score']
        p['v1_score']      = scores['final_score']
        p['final_score']   = scores['final_score']
        if scores.get('adaptive'):
            p['adaptive_calibration'] = scores['adaptive']
        try:
            zf = compute_zone_forecast(p.get('metrics', {}), p.get('btc_price'))
            if zf:
                p['zone_forecast'] = zf
                print(f"Zone prices: buy={zf['buy']['price']}  sell={zf['sell']['price']}  (realized={zf['realized_price']})")
        except Exception as e:
            print('Failed to compute zone forecast:', e)
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            print('Commentary: starting…', flush=True)
            try:
                from .gemini_commentary import generate_commentary
                sm = build_slider_map(p.get('metrics', {}))
                commentary = generate_commentary(p, sm)
                if commentary:
                    p['commentary'] = commentary
                    print('Commentary: OK', flush=True)
                else:
                    print('Commentary: generate_commentary returned None', flush=True)
            except Exception as e:
                print(f'Commentary: exception — {e}', flush=True)
        else:
            print('Commentary: skipped (local run)', flush=True)
        write_json('data/data.json', p)
        print(f"Scores: onchain={scores['onchain_score']}  tech={scores['tech_score']}  final={scores['final_score']}")
        for mk, mv in (scores.get('adaptive') or {}).items():
            print(f"  adaptive[{mk}]: fixed={mv['fixed']} adaptive={mv.get('adaptive')} -> blended={mv['blended']}")
    except Exception as e:
        print('Failed to compute scores:', e)

    # ── V2 scores (regime-based + TiZ) → data_exp.json ───────────────────────
    try:
        from .scoring_v2 import compute_scores_v2
        p_exp = copy.deepcopy(p)
        scores_v2 = compute_scores_v2(p.get('metrics', {}))
        p_exp['onchain_score']   = scores_v2['onchain_score']
        p_exp['tech_score']      = scores_v2['tech_score']
        p_exp['final_score']     = scores_v2['final_score']
        p_exp['scoring_regime']  = scores_v2['regime']
        p_exp['tiz_score']       = scores_v2['tiz_score']
        p_exp['tiz_days']        = scores_v2['tiz_days']
        p_exp['oc_coherence']    = scores_v2.get('oc_coherence')
        p_exp['coh_factor']      = scores_v2.get('coh_factor')
        p_exp['pi_cross']        = scores_v2.get('pi_cross')
        if scores_v2.get('adaptive'):
            p_exp['adaptive_calibration'] = scores_v2['adaptive']
        if scores_v2.get('wave_resonance'):
            p_exp['wave_resonance'] = scores_v2['wave_resonance']
        if scores_v2.get('signal'):
            p_exp['signal'] = scores_v2['signal']
        if build_metric_history_fn:
            p_exp['metric_history'] = build_metric_history_fn()
        write_json('data/data_exp.json', p_exp)
        p['v2_score']       = scores_v2['final_score']
        p['scoring_regime'] = scores_v2['regime']
        p['tiz_score']      = scores_v2['tiz_score']
        p['tiz_days']       = scores_v2['tiz_days']
        p['oc_coherence']   = scores_v2.get('oc_coherence')
        p['coh_factor']     = scores_v2.get('coh_factor')
        p['pi_cross']       = scores_v2.get('pi_cross')
        if scores_v2.get('wave_resonance'):
            p['wave_resonance'] = scores_v2['wave_resonance']
        _sig = scores_v2.get('signal')
        if _sig and _sig.get('meta_score') is not None:
            p['signal']      = _sig
            p['final_score'] = _sig['meta_score']
            print(f"Orchestrator: meta={_sig['meta_score']} flag={_sig.get('flag')} conv={_sig.get('conviction'):.2f}")
        else:
            print('Orchestrator: signal not available, keeping V1 final_score')
        wr  = scores_v2.get('wave_resonance', {})
        sig = scores_v2.get('signal', {})
        print(f"V2 scores: regime={scores_v2['regime']}  onchain={scores_v2['onchain_score']}  "
              f"tech={scores_v2['tech_score']}  tiz={scores_v2['tiz_score']}(day {scores_v2['tiz_days']})  "
              f"final={scores_v2['final_score']}")
        if wr.get('score') is not None:
            print(f"WR={wr['score']} coh={wr['coherence']}  "
                  f"Signal: meta={sig.get('meta_score')} conv={sig.get('conviction')} "
                  f"flag={sig.get('flag')}")
    except Exception as e:
        print(f'Failed to compute v2 scores: {e}')
        print('Falling back to V1 final_score')

    # ── V3 scores (dynamic z-weighted mixing) ─────────────────────────────────
    try:
        from .scoring_v3 import compute_scores_v3
        scores_v3 = compute_scores_v3(p.get('metrics', {}))
        p['v3_score']         = scores_v3['final_score']
        p['v3_onchain_score'] = scores_v3['onchain_avg']
        p['v3_tech_score']    = scores_v3['tech_avg']
        p['v3_phase']         = scores_v3['phase']
        p['v3_utilities']     = scores_v3['utilities']
        print(f"V3 scores: phase={scores_v3['phase']}  "
              f"onchain={scores_v3['onchain_avg']}  "
              f"tech={scores_v3['tech_avg']}  "
              f"final={scores_v3['final_score']}")
        try:
            p_exp['v3_score']         = scores_v3['final_score']
            p_exp['v3_onchain_score'] = scores_v3['onchain_avg']
            p_exp['v3_tech_score']    = scores_v3['tech_avg']
            p_exp['v3_phase']         = scores_v3['phase']
            p_exp['v3_utilities']     = scores_v3['utilities']
        except NameError:
            pass
    except Exception as e:
        print(f'Failed to compute v3 scores: {e}')

    # ── Fisher score → Orchestrator ───────────────────────────────────────────
    try:
        from .scoring import compute_scores_v2_fisher
        from .orchestrator import orchestrate
        import datetime as _dt_fs
        fisher_out = compute_scores_v2_fisher(p.get('metrics', {}))
        if fisher_out and fisher_out.get('final_score') is not None:
            p['fisher_score'] = fisher_out['final_score']
            _wr      = p.get('wave_resonance', {})
            _tiz     = p.get('tiz_days', 0)
            _tiz_cal = p.get('tiz_calibration', 200)
            _tiz_mat = round(_tiz / _tiz_cal, 3) if _tiz > 0 else None
            _top_sig, _bot_sig = None, None
            try:
                from .scoring_v2 import phase_signals as _ps_early, _METRIC_LOOKBACK as _ML_LB
                from .scoring import build_slider_map as _bsm
                from .wave_history import build_prev_scores_for_wave as _bpw
                _cs      = _bsm(p.get('metrics', {}))
                _ps_prev = _bpw(_dt_fs.date.today(), _ML_LB)
                _ps_out  = _ps_early(_cs, _ps_prev)
                _top_sig = _ps_out.get('top_signal')
                _bot_sig = _ps_out.get('bot_signal')
            except Exception:
                pass
            _fisher_sig = orchestrate(
                v2_score        = fisher_out['final_score'],
                v2_oc_coherence = fisher_out.get('oc_coherence', 1.0),
                wr_score        = _wr.get('score'),
                wr_coherence    = _wr.get('coherence'),
                tiz_maturity    = _tiz_mat,
                top_signal      = _top_sig,
                bot_signal      = _bot_sig,
                btc_price       = p.get('btc_price'),
                target_date     = _dt_fs.date.today()
            )
            p['signal']      = _fisher_sig
            p['final_score'] = _fisher_sig['meta_score']
            print(f"Fisher+Orch: fisher={fisher_out['final_score']}  "
                  f"meta={_fisher_sig['meta_score']}  "
                  f"flag={_fisher_sig.get('flag')}  "
                  f"conv={_fisher_sig.get('conviction', 0):.2f}  "
                  f"geo={_top_sig}/{_bot_sig}")
    except Exception as _fe:
        print(f'Fisher scorer skipped: {_fe}')

    # ── SP-v2 + Phase Signals + V3.2 Override (master index) ─────────────────
    try:
        from .scoring_v2 import score_processor_v2, phase_signals, _METRIC_LOOKBACK
        from .scoring import score_from_raw, build_slider_map
        from .wave_history import build_prev_scores_for_wave
        from .scoring_v3 import compute_scores_v3
        from .orchestrator import orchestrate
        import datetime as _dt

        today       = _dt.date.today()
        curr_scores = build_slider_map(p.get('metrics', {}))
        prev_scores = build_prev_scores_for_wave(today, _METRIC_LOOKBACK)
        sp_v2       = score_processor_v2(curr_scores, prev_scores)
        phases      = phase_signals(curr_scores, prev_scores)

        try:
            scores_v3   = compute_scores_v3(p.get('metrics', {}))
            _wr         = p.get('wave_resonance', {})
            _tiz_mat_v3 = scores_v3.get('tiz_maturity')
            v3_sig = orchestrate(
                v2_score        = scores_v3['final_score'],
                v2_oc_coherence = scores_v3.get('oc_coherence', 1.0),
                wr_score        = _wr.get('score'),
                wr_coherence    = _wr.get('coherence'),
                tiz_maturity    = _tiz_mat_v3,
                top_signal      = scores_v3.get('top_signal'),
                bot_signal      = scores_v3.get('bot_signal'),
                is_v3           = True,
                btc_price       = p.get('btc_price'),
                target_date     = today
            )
            p['v3_score']           = scores_v3['final_score']
            p['v3_onchain_score']   = scores_v3['onchain_avg']
            p['v3_tech_score']      = scores_v3['tech_avg']
            p['v3_phase']           = scores_v3['phase']
            p['v3_w_bot']           = scores_v3['w_bot']
            p['v3_w_neutral']       = scores_v3['w_neutral']
            p['v3_w_top']           = scores_v3['w_top']
            p['v3_utilities']       = scores_v3['utilities']
            p['v3_signal']          = v3_sig
            p['v3_tiz_score']       = scores_v3['tiz_score']
            p['v3_tiz_days']        = scores_v3['tiz_days']
            p['v3_tiz_maturity']    = scores_v3['tiz_maturity']
            p['v3_tiz_calibration'] = scores_v3['tiz_calibration']
            p['v3_oc_coherence']    = scores_v3['oc_coherence']
            p['final_score']        = v3_sig['meta_score']
            p['signal']             = v3_sig
            p['onchain_score']      = scores_v3['onchain_avg']
            p['tech_score']         = scores_v3['tech_avg']
            p['sp_v2']              = scores_v3['final_score']
            p['phase'] = {
                'phase':      scores_v3['phase'],
                'top_signal': scores_v3['top_signal'],
                'bot_signal': scores_v3['bot_signal'],
                'w_bot':      scores_v3['w_bot'],
                'w_neutral':  scores_v3['w_neutral'],
                'w_top':      scores_v3['w_top'],
            }
            try:
                p_exp['v3_score']           = scores_v3['final_score']
                p_exp['v3_onchain_score']   = scores_v3['onchain_avg']
                p_exp['v3_tech_score']      = scores_v3['tech_avg']
                p_exp['v3_phase']           = scores_v3['phase']
                p_exp['v3_w_bot']           = scores_v3['w_bot']
                p_exp['v3_w_neutral']       = scores_v3['w_neutral']
                p_exp['v3_w_top']           = scores_v3['w_top']
                p_exp['v3_utilities']       = scores_v3['utilities']
                p_exp['v3_signal']          = v3_sig
                p_exp['v3_tiz_score']       = scores_v3['tiz_score']
                p_exp['v3_tiz_days']        = scores_v3['tiz_days']
                p_exp['v3_tiz_maturity']    = scores_v3['tiz_maturity']
                p_exp['v3_tiz_calibration'] = scores_v3['tiz_calibration']
                p_exp['v3_oc_coherence']    = scores_v3['oc_coherence']
                p_exp['final_score']        = v3_sig['meta_score']
                p_exp['signal']             = v3_sig
                p_exp['onchain_score']      = scores_v3['onchain_avg']
                p_exp['tech_score']         = scores_v3['tech_avg']
                p_exp['sp_v2']              = scores_v3['final_score']
                p_exp['phase']              = p['phase']
            except NameError:
                pass
            print(f"V3.2 Overwrite Success: final={p['final_score']} phase={scores_v3['phase']}")
        except Exception as ve:
            print(f"Failed to override V3.2 scores: {ve}")

        write_json('data/data.json', p)
        try:
            write_json('data/data_exp.json', p_exp)
        except NameError:
            pass
        print(f"SP-v2: score={sp_v2}  "
              f"top={phases.get('top_signal')}%  "
              f"bot={phases.get('bot_signal')}%  "
              f"phase={phases.get('phase')}")
    except Exception as e:
        print(f'Failed to compute SP-v2: {e}')

    return p
