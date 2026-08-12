"""Full scoring pipeline: V1 → V2 → V3 → Fisher+Orch → V3.2 override.

Call run_scoring_pipeline(p, build_metric_history_fn) from scraper.main().
Modifies p in place, writes data.json / data_exp.json, returns p.
"""
import copy
import datetime
import os

from .utils import write_json


def run_scoring_pipeline(p, build_metric_history_fn=None):
    """Run all scoring passes on payload p. Returns updated p."""

    _v1_archive: dict | None = None   # captured for archive section
    _v2_archive: dict | None = None   # captured for archive section

    # ── V1 scores ─────────────────────────────────────────────────────────────
    try:
        from .scoring import compute_scores, build_slider_map
        from .zone_forecast import compute_zone_forecast
        scores = compute_scores(p.get('metrics', {}))
        p['onchain_score'] = scores['onchain_score']
        p['tech_score']    = scores['tech_score']
        p['final_score']   = scores['final_score']
        _v1_archive = {
            'onchain_score': scores.get('onchain_score'),
            'tech_score':    scores.get('tech_score'),
            'final_score':   scores.get('final_score'),
        }
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
            # Reuse today's commentary if home server already generated it
            _today_str = datetime.date.today().isoformat()
            _cached_commentary = None
            try:
                import json as _json
                with open('data/data.json', encoding='utf-8') as _f:
                    _prev = _json.load(_f)
                if (_prev.get('commentary') and
                        (_prev.get('date') == _today_str or
                         (_prev.get('timestamp', '') or '')[:10] == _today_str)):
                    _cached_commentary = _prev['commentary']
            except Exception:
                pass
            if _cached_commentary:
                p['commentary'] = _cached_commentary
                print('Commentary: reused from today\'s cached run', flush=True)
            else:
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
        _v2_archive = {
            'onchain_score': scores_v2.get('onchain_score'),
            'tech_score':    scores_v2.get('tech_score'),
            'final_score':   scores_v2.get('final_score'),
            'regime':        scores_v2.get('regime'),
            'tiz_score':     scores_v2.get('tiz_score'),
        }
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

    # ── Clean V3 Master Score ─────────────────────────────────────────────────
    try:
        from .score import compute_score
        from .orchestrator import orchestrate
        import datetime as _dt

        today   = _dt.date.today()
        scores  = compute_score(
            raw_metrics   = p.get('metrics', {}),
            target_date   = today,
            btc_price     = p.get('btc_price'),
        )
        _wr = scores.get('wave_resonance', {}) or {}
        v3_sig = orchestrate(
            v2_score        = scores['final_score'],
            v2_oc_coherence = scores.get('oc_coherence', 1.0),
            wr_score        = _wr.get('score'),
            wr_coherence    = _wr.get('coherence'),
            tiz_maturity    = scores.get('tiz_maturity'),
            top_signal      = scores.get('top_signal'),
            bot_signal      = scores.get('bot_signal'),
            is_v3           = True,
            btc_price       = p.get('btc_price'),
            target_date     = today,
        )

        # Guard: overheated funding contradicts CONFIRMED_BOTTOM
        _fr_score = scores.get('normalized_scores', {}).get('funding_rate')
        if _fr_score is not None and _fr_score > 80 and v3_sig.get('flag') == 'CONFIRMED_BOTTOM':
            v3_sig = dict(v3_sig)
            v3_sig['flag'] = 'PROBABLE_BOTTOM'

        _EXPECTED_SCORING_METRICS = {
            'nupl', 'mvrv_z_score', 'rhodl_ratio', 'cvdd_ratio', 'asopr', 'puell',
            'lth_supply', 'mayer_multiple', 'fear_greed', 'm2_yoy', 'yield_curve_spread',
            'etf_flows', 'cipherb', 'funding_rate', 'btc_price_cycle',
            # pi_gap intentionally excluded: returns None when gap > 20% (not firing)
        }
        _norm    = scores.get('normalized_scores', {})
        _active  = [k for k in _EXPECTED_SCORING_METRICS if _norm.get(k) is not None]
        _missing = sorted(k for k in _EXPECTED_SCORING_METRICS if _norm.get(k) is None)

        p['v3_score']             = scores['final_score']
        p['v3_onchain_score']     = scores['onchain_avg']
        p['v3_tech_score']        = scores['tech_avg']
        p['v3_phase']             = scores['phase']
        p['v3_w_bot']             = scores['w_bot']
        p['v3_w_neutral']         = scores['w_neutral']
        p['v3_w_top']             = scores['w_top']
        p['v3_utilities']         = scores['utilities']
        p['v3_normalized_scores'] = _norm
        p['v3_dxy_adj']           = {'score': _norm.get('dxy'), 'adjustment': scores.get('dxy_adj', 0)}
        p['v3_signal']            = v3_sig
        p['v3_tiz_score']         = scores['tiz_score']
        p['v3_tiz_days']          = scores['tiz_days']
        p['v3_tiz_maturity']      = scores['tiz_maturity']
        p['v3_tiz_calibration']   = scores['tiz_calibration']
        p['v5_score']             = scores.get('v5_score')
        p['v5b_score']            = scores.get('v5b_score')
        p['meta_score']           = scores.get('meta_score')
        p['signal_agreement']     = scores.get('signal_agreement')
        p['market_regime']        = scores.get('market_regime')
        p['composite_risk']       = scores.get('composite_risk')
        p['v3_oc_coherence']      = scores['oc_coherence']
        p['wave_resonance']        = _wr
        p['data_quality'] = {
            'active_metrics': len(_active),
            'total_metrics':  len(_EXPECTED_SCORING_METRICS),
            'quality_pct':    round(len(_active) / len(_EXPECTED_SCORING_METRICS) * 100),
            'missing_metrics': _missing,
        }
        p['final_score']   = scores['final_score']
        p['signal']        = v3_sig
        p['onchain_score'] = scores['onchain_avg']
        p['tech_score']    = scores['tech_avg']
        p['sp_v2']         = scores['final_score']
        p['phase'] = {
            'phase':      scores['phase'],
            'top_signal': scores['top_signal'],
            'bot_signal': scores['bot_signal'],
            'w_bot':      scores['w_bot'],
            'w_neutral':  scores['w_neutral'],
            'w_top':      scores['w_top'],
        }

        # ── Build 3-layer structured sections ─────────────────────────────────
        # Layer 1 — base: raw values + normalized scores (immutable after scraping)
        _norm = scores.get('normalized_scores', {})
        _metrics_raw = p.get('metrics', {})
        _raw_to_norm = {
            'nupl': 'nupl', 'mvrv': 'mvrv_z_score', 'rhodl_ratio': 'rhodl_ratio',
            'cvdd_ratio': 'cvdd_ratio', 'asopr': 'asopr', 'puell_multiple': 'puell',
            'mayer_multiple': 'mayer_multiple', 'fear_greed': 'fear_greed',
            'm2_mom': 'm2_yoy', 'yield_curve': 'yield_curve_spread',
            'etf_flows': 'etf_flows', 'funding_rate': 'funding_rate',
            'dxy': 'dxy', 'lth_supply_pct': 'lth_supply',
        }

        def _raw_val(key):
            obj = _metrics_raw.get(key)
            if isinstance(obj, dict):
                return obj.get('value')
            return obj

        _base = {'btc_price': p.get('btc_price')}
        for raw_k, norm_k in _raw_to_norm.items():
            _base[raw_k] = {'raw': _raw_val(raw_k), 'score': _norm.get(norm_k)}

        # Layer 2 — computed: baskets, phase, coherence, tiz, utilities
        _computed = {
            'baskets': {
                'OC': scores.get('oc_basket'),
                'MS': scores.get('ms_basket'),
                'MC': scores.get('mc_basket'),
                'CP': scores.get('cp_basket'),
            },
            'phase': {
                'label':  scores['phase'],
                'w_bot':  scores['w_bot'],
                'w_neu':  scores['w_neutral'],
                'w_top':  scores['w_top'],
            },
            'OC_read':  scores.get('oc_read'),
            'coherence': {
                'oc_score': scores.get('oc_coherence'),
                'factor':   scores.get('coh_factor'),
            },
            'tiz': {
                'score':       scores.get('tiz_score'),
                'days':        scores.get('tiz_days'),
                'maturity':    scores.get('tiz_maturity'),
                'calibration': scores.get('tiz_calibration'),
            },
            'utilities': scores.get('utilities', {}),
        }

        # Layer 3 — scoring: final formula outputs + V5
        _scoring = {
            'final_score':   scores['final_score'],
            'v5_score':      scores.get('v5_score'),
            'v5b_score':     scores.get('v5b_score'),
            'meta_score':       scores.get('meta_score'),
            'signal_agreement': scores.get('signal_agreement'),
            'market_regime':    scores.get('market_regime'),
            'composite_risk':   scores.get('composite_risk'),
            'dxy_adj':       scores.get('dxy_adj'),
            'pi_cross':      scores.get('pi_cross'),
            'flag':          v3_sig.get('flag'),
            'conviction':    v3_sig.get('conviction'),
        }

        # Archive: V1/V2 for efficiency comparison
        _archive = {
            'v1': _v1_archive,
            'v2': _v2_archive,
        }

        p['base']     = _base
        p['computed'] = _computed
        p['scoring']  = _scoring
        p['archive']  = _archive

        write_json('data/data.json', p)
        # Mirror to web/ so the site always reflects the latest scraper run
        try:
            write_json('web/data.json', p)
        except Exception:
            pass
        try:
            write_json('data/data_exp.json', p)
            write_json('web/data_exp.json', p)
        except Exception:
            pass

        # ── Append today's entry to scores.json so TiZ advances each day ────
        try:
            import json as _json
            _scores_path = 'data/history/scores.json'
            _today_str   = today.isoformat()
            _new_entry   = {
                'date':        _today_str,
                'final_score': scores['final_score'],
                'v5_score':    scores.get('v5_score'),
                'v1_score':    _v1_archive.get('final_score') if _v1_archive else None,
                'v2_score':    _v2_archive.get('final_score') if _v2_archive else None,
                'phase':       scores['phase'],
                'w_bot':       scores['w_bot'],
                'w_top':       scores['w_top'],
                'btc_price':   p.get('btc_price'),
            }
            _hist: list = []
            if os.path.exists(_scores_path):
                try:
                    with open(_scores_path, encoding='utf-8') as _f:
                        _hist = _json.load(_f)
                except Exception:
                    pass
            # Replace or append today's entry (deduplicate by date)
            _hist = [e for e in _hist if e.get('date') != _today_str]
            _hist.append(_new_entry)
            _hist.sort(key=lambda e: e.get('date', ''))
            write_json(_scores_path, _hist)
            print(f"scores.json: appended {_today_str} ({len(_hist)} total entries)")
            # Mirror to web/ immediately
            try:
                import subprocess as _sp
                _sp.run(['python3', 'tools/sync_web_scores.py'], check=False)
            except Exception:
                pass
        except Exception as _e:
            print(f'scores.json update failed: {_e}')

        print(
            f"V3 Clean: final={p['final_score']}  pre_orch={scores['final_score']}"
            f"  phase={scores['phase']}  w_bot={scores['w_bot']}"
            f"  tiz={scores['tiz_score']}({scores['tiz_days']}d)"
            f"  wr={_wr.get('score')}  dxy_adj={scores.get('dxy_adj', 0):+.1f}"
            f"  v5={scores.get('v5_score')}  v5b={scores.get('v5b_score')}  meta={scores.get('meta_score')}(agree={scores.get('signal_agreement')})  regime={scores.get('market_regime')}"
        )
    except Exception as e:
        print(f'V3 Clean scorer failed: {e}')
        import traceback; traceback.print_exc()

    return p
