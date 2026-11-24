import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.allocation_engine import compute_allocation_signal
from scraper.score import compute_score


def test_allocation_engine_causal_d1_dual_sleeve():
    # Sleeve 1 (SMA40w) + Sleeve 2 (Cipher B 1W ±2.0 + real MVRV Value-Veto)
    a1 = compute_allocation_signal(
        30,
        [('2026-11-01', 40)],
        {'wt1': -5, 'wt2': 0},
        sma40_in_pos=True,
        mvrv_pct=0.20,
        value_sell_veto=True,
    )
    assert a1['allocation_engine'] == 'causal_d1_sma40_cb_veto'
    assert a1['sma40_sleeve_pct'] == 50
    assert a1['cipherb_sleeve_pct'] == 0
    assert a1['target_allocation_pct'] == 50
    assert a1['market_regime'] == 'Buy'

    # Both sleeves active in bull trend
    a2 = compute_allocation_signal(
        87,
        [('2026-11-01', 30)],
        {'wt1': 10, 'wt2': 5},
        sma40_in_pos=True,
        mvrv_pct=0.75,
    )
    assert a2['trailing_armed'] is True  # legacy archive diagnostic preserved
    assert a2['target_allocation_pct'] == 100
    assert a2['market_regime'] == 'Hold'

    # Both sleeves out in overheated regime -> Sell / EXIT_CASH
    a3 = compute_allocation_signal(
        71,
        [('2026-11-01', 30), ('2026-11-02', 87)],
        {'wt1': -5, 'wt2': 0},
        sma40_in_pos=False,
        mvrv_pct=0.75,
    )
    assert a3['target_allocation_pct'] == 0
    assert a3['market_regime'] == 'Sell'
    assert a3['action_label'] == 'EXIT_CASH'

    # Invalid data quality -> Unavailable
    a_inv = compute_allocation_signal(30, [], {'wt1': 0, 'wt2': 0}, data_quality_status='invalid')
    assert a_inv['market_regime'] == 'Unavailable'


def test_no_retired_v5_keys():
    raw = {
        'btc_price': 61360.89,
        'nupl': 12.73,
        'mvrv': 0.26,
        'rhodl_ratio': 691.0,
        'cvdd_ratio': 1.20,
        'asopr': 0.9829,
        'puell_multiple': 0.71,
        'mayer_multiple': 0.846,
        'fear_greed': 20,
        'm2_mom': 5.0,
        'yield_curve': 0.2,
        'etf_flows': -200.0,
        'funding_rate': 0.002,
        'dxy': 118.0,
        'lth_supply_pct': 70.0,
        'pi_cycle': 49.0,
        'cipherb': {'weekly_score': 40.6, 'daily_score': 0.0, 'wt1': -2.1, 'wt2': 19.8},
    }
    res = compute_score(raw_metrics=raw, target_date=datetime.date(2026, 11, 23), btc_price=61360.89, include_v5b=False)
    for retired in ('v5_score', 'v5_confidence', 'v5_shap_top5', 'legacy_model'):
        assert retired not in res
    assert res['bri_version'] == 'v5'
    assert res['target_allocation_pct'] in (0, 50, 100)
