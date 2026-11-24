"""Unit tests for Stage 1 & Stage 2 integrity fixes (zero lookahead, hysteresis, PiT ledger, 13w tail risk)."""
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_allocation_engine_true_hysteresis():
    from scraper.allocation_engine import compute_allocation_signal

    # Deadband spread = +0.5 (between -2.0 and +2.0): must preserve prev_cb_in_pos
    cb = {'wt1': 10.5, 'wt2': 10.0, 'weekly_score': 52.0}
    res_keep_in = compute_allocation_signal(50, cipherb_val=cb, prev_cb_in_pos=True)
    res_keep_out = compute_allocation_signal(50, cipherb_val=cb, prev_cb_in_pos=False)
    assert res_keep_in['cipherb_sleeve_pct'] == 50
    assert res_keep_out['cipherb_sleeve_pct'] == 0

    # Outside deadband (+2.5 and -2.5): overrides prev_cb_in_pos
    res_enter = compute_allocation_signal(50, cipherb_val={'wt1': 12.5, 'wt2': 10.0}, prev_cb_in_pos=False)
    res_exit = compute_allocation_signal(50, cipherb_val={'wt1': 7.5, 'wt2': 10.0}, prev_cb_in_pos=True)
    assert res_enter['cipherb_sleeve_pct'] == 50
    assert res_exit['cipherb_sleeve_pct'] == 0


def test_wave_resonance_no_future_lookahead(monkeypatch):
    from scraper import wave_resonance as wr

    # Only future date '2026-01-05' exists relative to target '2026-01-02'; must NOT pick '2026-01-05'
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


def test_pit_ledger_append_only(tmp_path):
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
        'market_regime': 'Buy',
        'metrics': {'nupl': {'value': 13.0, 'source': 'BMP', 'updated': '2026-11-24T16:00:00Z', 'is_fallback': False}},
    }
    append_pit_record(payload, ledger_path=str(ledger))
    append_pit_record(payload, ledger_path=str(ledger))
    lines = ledger.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec['as_of_date'] == '2026-11-24'
    assert rec['forward_risk_13w'] == 22.5
    assert rec['tail_risk_shadow_m2_pct'] == 21.0
    assert 'observed_at_utc' in rec


def test_tail_risk_13w_causal_on_repo_history():
    from scraper.tail_risk import predict_tail_risk_13w

    res = predict_tail_risk_13w(
        btc_price_today=60000.0,
        mvrv_today=0.3,
        target_date=datetime.date(2026, 11, 24),
        scores_path='data/history/scores.json',
    )
    assert res['validated'] is True
    assert res['method'] == 'causal_vol13_logistic'
    assert 0.0 <= res['score'] <= 100.0
    assert res['train_n'] >= 200

    # Missing/insufficient history must fail cleanly without silent heuristic fallback
    empty_res = predict_tail_risk_13w(scores_path='data/nonexistent_scores_xyz.json')
    assert empty_res['validated'] is False
    assert empty_res['score'] is None
    assert empty_res['method'] == 'insufficient_history'


def test_causal_core_valuation_and_checklist():
    from scraper.causal_core import build_entry_exit_checklist, compute_causal_mvrv_rank

    mv_info = compute_causal_mvrv_rank(0.25, as_of_date='2026-11-24', scores_path='data/history/scores.json')
    assert mv_info['valuation_score'] is not None
    assert 0.0 <= mv_info['valuation_score'] <= 100.0
    assert mv_info['history_n'] > 1000

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
        mvrv_info={'valuation_score': 12.0, 'mvrv_pct': 0.12, 'value_sell_veto': True},
        tail_risk={'validated': True, 'prob_dd25_13w_pct': 24.0},
        data_quality={'status': 'valid'},
    )
    assert len(cl['items']) == 11
    assert cl['abstain'] is True
    assert 'value_vs_momentum_conflict' in cl['abstain_reasons']
