"""Unit tests for Causal v2 Core integrity (zero lookahead, real OHLC tail risk, D1 allocator, PiT ledger)."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_allocation_engine_true_hysteresis_and_real_value_veto():
    from scraper.allocation_engine import compute_allocation_signal

    # Deadband spread = +0.5 (between -2.0 and +2.0): must preserve prev_cb_in_pos
    cb = {'wt1': 10.5, 'wt2': 10.0, 'weekly_score': 52.0}
    res_keep_in = compute_allocation_signal(50, cipherb_val=cb, prev_cb_in_pos=True, sma40_in_pos=False)
    res_keep_out = compute_allocation_signal(50, cipherb_val=cb, prev_cb_in_pos=False, sma40_in_pos=False)
    assert res_keep_in['cipherb_sleeve_pct'] == 50
    assert res_keep_out['cipherb_sleeve_pct'] == 0

    # Outside deadband (+2.5 and -2.5): overrides prev_cb_in_pos when veto is OFF
    res_enter = compute_allocation_signal(50, cipherb_val={'wt1': 12.5, 'wt2': 10.0}, prev_cb_in_pos=False, sma40_in_pos=False)
    res_exit = compute_allocation_signal(50, cipherb_val={'wt1': 7.5, 'wt2': 10.0}, prev_cb_in_pos=True, sma40_in_pos=False, value_sell_veto=False)
    assert res_enter['cipherb_sleeve_pct'] == 50
    assert res_exit['cipherb_sleeve_pct'] == 0

    # REAL Value Sell-Veto: blocks Cipher B exit when prev_cb_in_pos=True and value_sell_veto=True
    res_veto = compute_allocation_signal(
        30,
        cipherb_val={'wt1': 7.5, 'wt2': 10.0},
        prev_cb_in_pos=True,
        sma40_in_pos=False,
        value_sell_veto=True,
        mvrv_pct=0.15,
    )
    assert res_veto['cipherb_sleeve_pct'] == 50
    assert res_veto['veto_blocked_exit'] is True
    assert res_veto['action_label'] == 'VALUE_VETO_HOLD_50'
    assert res_veto['market_regime'] == 'Hold'

    # Clean abstain: never contradicts itself by outputting Buy when abstain=True
    abstain_res = compute_allocation_signal(
        20,
        cipherb_val={'wt1': 15.0, 'wt2': 5.0},
        sma40_in_pos=True,
        mvrv_pct=0.15,
        abstain=True,
    )
    assert abstain_res['abstain'] is True
    assert abstain_res['market_regime'] == 'Wait'
    assert abstain_res['action_label'] == 'ABSTAIN_UNCERTAIN'

    # Cheap valuation + both sleeves out -> ACCUMULATE_WATCH (not Sell)
    watch_res = compute_allocation_signal(
        24,
        cipherb_val={'wt1': -20.0, 'wt2': 0.0},
        prev_cb_in_pos=False,
        sma40_in_pos=False,
        value_sell_veto=True,
        mvrv_pct=0.12,
        abstain=False,
    )
    assert watch_res['target_allocation_pct'] == 0
    assert watch_res['market_regime'] == 'Wait'
    assert watch_res['action_label'] == 'ACCUMULATE_WATCH'


def test_wave_resonance_no_future_lookahead(monkeypatch):
    from scraper import wave_resonance as wr

    fake_unified = {
        '2025-12-20': {'nupl': 20.0, 'mvrv': 1.0, 'rhodl_ratio': 500.0, 'mayer_multiple': 0.9},
        '2026-01-05': {'nupl': 80.0, 'mvrv': 5.0, 'rhodl_ratio': 5000.0, 'mayer_multiple': 2.0},
    }
    monkeypatch.setattr(wr, '_load_unified', lambda: fake_unified)
    out = wr.compute_wave_resonance(date='2026-01-02')
    assert out['date_used'] == '2025-12-20'


def test_maps_load_metric_history_deduplicates_dates():
    from scraper import maps

    maps.invalidate_caches()
    pts = maps._load_metric_history('nupl')
    dates = [d for d, _ in pts]
    assert len(dates) == len(set(dates)), 'Duplicate dates found in _load_metric_history'


def test_pit_ledger_append_only_and_m2_obs_propagation(tmp_path):
    from scraper.pit_ledger import append_pit_record

    ledger = tmp_path / 'pit_ledger.jsonl'
    payload = {
        'timestamp': '2026-11-24T16:19:05Z',
        'date': '2026-11-24',
        'btc_price': 60000.0,
        'bri_score': 24,
        'v5b_score': 22.5,
        'v5b_validated': True,
        'forward_risk_method': 'causal_vol13_logistic',
        'tail_risk_13w': {'shadow_m2_pct': 21.0},
        'market_regime': 'Wait',
        'metrics': {
            'nupl': {'value': 13.0, 'source': 'BMP', 'updated': '2026-11-24T16:00:00Z', 'is_fallback': False},
            'm2': {'value': 8.2, 'source': 'MacroMicro', 'observation_date': '2026-09-01', 'is_fallback': False},
            'm2_mom': {'value': 8.2, 'source': 'MacroMicro', 'observation_date': None, 'is_fallback': False},
        },
    }
    append_pit_record(payload, ledger_path=str(ledger))
    append_pit_record(payload, ledger_path=str(ledger))
    lines = ledger.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec['as_of_date'] == '2026-11-24'
    assert rec['forward_risk_13w'] == 22.5
    assert rec['tail_risk_shadow_m2_pct'] == 21.0
    assert rec['raw_metrics']['m2_mom']['observation_date'] == '2026-09-01'
    assert 'observed_at_utc' in rec


def test_tail_risk_13w_real_ohlc_passes_oos_validation():
    from scraper.tail_risk import predict_tail_risk_13w

    res = predict_tail_risk_13w(
        btc_price_today=60000.0,
        mvrv_today=0.3,
        target_date=datetime.date(2026, 11, 24),
        scores_path='data/history/scores.json',
        ohlc_path='data/history/btc_weekly_ohlc.json',
    )
    assert res['method'] == 'causal_vol13_logistic'
    assert res['ohlc_source'] == 'weekly_ohlc_archive'
    assert 0.0 <= res['score'] <= 100.0
    assert res['train_n'] >= 500
    assert isinstance(res['oos_validation'], dict)
    assert res['oos_validation']['n_oos'] >= 450
    assert res['oos_validation']['bss_vs_prevalence'] > 0.0
    assert res['oos_validation']['ece_5bin'] <= 0.08
    assert res['validated'] is True

    # Closed-week invariance: Tuesday partial week vs Monday after Sunday close must match
    res_mon = predict_tail_risk_13w(target_date=datetime.date(2026, 11, 23))
    res_tue = predict_tail_risk_13w(btc_price_today=999999.0, target_date=datetime.date(2026, 11, 24))
    assert res_mon['vol13_ann_pct'] == res_tue['vol13_ann_pct']
    assert res_mon['score'] == res_tue['score']

    # Missing/insufficient history must fail cleanly without silent heuristic fallback
    empty_res = predict_tail_risk_13w(scores_path='data/nonexistent_scores_xyz.json', ohlc_path='data/nonexistent_ohlc_xyz.json')
    assert empty_res['validated'] is False
    assert empty_res['score'] is None
    assert empty_res['method'] == 'insufficient_history'


def test_causal_core_d1_state_and_clean_checklist():
    from scraper.causal_core import (
        build_entry_exit_checklist,
        compute_causal_d1_state,
        compute_causal_mvrv_rank,
        infer_prev_cipherb_state,
    )

    # Empty metrics MUST trigger abstain (never CLEAR)
    empty_cl = build_entry_exit_checklist({}, {'valuation_score': None, 'mvrv_pct': None}, None, {'status': 'valid'})
    assert empty_cl['abstain'] is True
    assert 'insufficient_metrics' in empty_cl['abstain_reasons']

    # Live D1 state on closed weekly OHLC
    d1 = compute_causal_d1_state(as_of_date='2026-11-24')
    assert d1['available'] is True
    assert d1['last_closed_week'] == '2026-11-16'
    assert isinstance(d1['sma40_in_pos'], bool)
    assert isinstance(d1['cb_in_pos'], bool)

    mv_info = compute_causal_mvrv_rank(0.25, as_of_date='2026-11-24', puell_today=0.95, cvdd_today=1.20)
    assert mv_info['valuation_score'] is not None
    assert mv_info['cost_basis_components'] == 3
    assert mv_info['history_n'] > 5000

    # Validated risk + full metrics + cheap falling momentum -> CLEAR with regime_note (NOT abstain!)
    cl = build_entry_exit_checklist(
        raw_metrics={
            'mvrv': {'value': 0.25, 'is_fallback': False},
            'cipherb': {'value': {'wt1': -2.0, 'wt2': 19.0}},
            'puell_multiple': {'value': 0.95},
            'cvdd_ratio': {'value': 1.20},
            'asopr': {'value': 0.98},
            'fear_greed': {'value': 10.0},
            'funding_rate': {'value': {'avg_7d': 0.002}},
            'etf_flows': {'value': {'value': -500.0}},
            'dxy': {'value': 123.5, 'observation_date': '2026-11-20'},
            'm2_mom': {'value': 8.2, 'observation_date': '2026-09-01'},
            'mayer_multiple': {'value': {'value': 0.84}},
        },
        mvrv_info=mv_info,
        tail_risk={'validated': True, 'prob_dd25_13w_pct': 24.0},
        data_quality={'status': 'valid'},
        as_of_date='2026-11-24',
        d1_state=d1,
    )
    assert len(cl['items']) == 12
    assert cl['abstain'] is False
    assert 'cheap_falling_momentum' in cl['regime_notes']
    assert infer_prev_cipherb_state('2026-11-12') in (True, False)


def test_data_json_synced_with_live_compute_score():
    from scraper.score import compute_score

    d = json.load(open('data/data.json', encoding='utf-8'))
    scores_hist = [
        (r['date'], r.get('final_score'), r.get('phase'), r.get('w_bot'))
        for r in json.load(open('data/history/scores.json', encoding='utf-8'))
        if r.get('date') and r['date'][:10] < d['date'][:10]
    ]
    live = compute_score(
        raw_metrics=d['metrics'],
        scores_history=scores_hist,
        target_date=datetime.date.fromisoformat(d['date'][:10]),
        btc_price=d.get('btc_price'),
        persist_hmm_cache=False,
    )
    assert d['allocation_signal']['allocation_engine'] == 'causal_d1_sma40_cb_veto'
    assert d['allocation_signal']['mode_100_target_pct'] in (0, 100)
    assert d['allocation_signal']['mode_100_target_pct'] == live['allocation_signal']['mode_100_target_pct']
    assert d['allocation_signal']['ohlc_bar_date'] == '2026-11-16'
    assert d['allocation_signal']['weekly_wt_spread'] == live['allocation_signal']['weekly_wt_spread']
    assert d['allocation_signal']['action_label'] == live['allocation_signal']['action_label']
    assert d['causal_btcbri'] == live['causal_btcbri']
    assert 0.0 <= d['causal_btcbri'] <= 100.0
    assert isinstance(d.get('cycle_amplitude'), dict)
    assert d['cycle_amplitude']['dd_from_ath_pct'] <= 0.0
    assert 0.0 <= d['cycle_amplitude']['amp_score'] <= 100.0
    assert d['conflict_score'] == live['conflict_score']
    assert d['confidence_score'] == 100 - d['conflict_score']
    assert d['conflict_score'] < 50  # no longer inflated to 52-95 by anti-phase DXY/Macro noise
    assert d['allocation_signal']['target_allocation_pct'] == live['allocation_signal']['target_allocation_pct']
    assert d['v5b_validated'] == live['v5b_validated'] is True
    assert d['entry_exit_checklist']['abstain'] == live['entry_exit_checklist']['abstain']
