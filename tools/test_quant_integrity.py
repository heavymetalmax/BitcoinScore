import datetime

from scraper import cycle_normalizer
from scraper.bottom_confluence import compute_bottom_confluence
from scraper.score import assess_data_quality
from tools.build_v5b_labels import max_drawdown_365
from tools.train_v5b_model import PURGE_DAYS, make_purged_folds


def test_forward_label_rejects_partial_horizon():
    start = datetime.date(2025, 1, 1)
    prices = {(start + datetime.timedelta(days=i)).isoformat(): 100.0 for i in range(200)}
    assert max_drawdown_365(start.isoformat(), prices) is None


def test_forward_label_uses_complete_horizon():
    start = datetime.date(2024, 1, 1)
    prices = {(start + datetime.timedelta(days=i)).isoformat(): 100.0 for i in range(366)}
    prices[(start + datetime.timedelta(days=180)).isoformat()] = 60.0
    assert max_drawdown_365(start.isoformat(), prices) == 40.0


def test_walk_forward_folds_are_purged():
    start = datetime.date(2015, 1, 1)
    dates = [(start + datetime.timedelta(days=i)).isoformat() for i in range(2500)]
    for _, train_idx, test_idx, _ in make_purged_folds(dates):
        train_end = datetime.date.fromisoformat(dates[int(train_idx[-1])])
        test_start = datetime.date.fromisoformat(dates[int(test_idx[0])])
        assert (test_start - train_end).days >= PURGE_DAYS


def test_cycle_anchor_is_hidden_until_confirmation(monkeypatch):
    monkeypatch.setattr(cycle_normalizer, '_extremes', [{
        'date': '2020-01-01', 'confirmed_at': '2020-03-01', 'type': 'BOTTOM'
    }])
    monkeypatch.setattr(cycle_normalizer, '_scores_map', {'2020-01-01': {'nupl': 1.5}})
    assert cycle_normalizer._causal_vals('nupl', 'BOTTOM', '2020-02-01') == []
    assert cycle_normalizer._causal_vals('nupl', 'BOTTOM', '2020-03-01') == [1.5]


def test_bottom_confluence_requires_quorum():
    cal = {
        'bottom_max': {m: 10 for m in ('nupl', 'mvrv_z_score', 'cvdd_ratio', 'mayer_multiple')},
        'neutral_min': {m: 50 for m in ('nupl', 'mvrv_z_score', 'cvdd_ratio', 'mayer_multiple')},
    }
    assert compute_bottom_confluence({'nupl': 10, 'mvrv_z_score': 10, 'cvdd_ratio': 10}, cal) is None
    score = compute_bottom_confluence(
        {'nupl': 10, 'mvrv_z_score': 10, 'cvdd_ratio': 10, 'mayer_multiple': 10}, cal
    )
    assert score == 67


def test_actionable_score_requires_all_basket_quorums():
    valid = {
        'nupl': 1, 'mvrv_z_score': 1, 'rhodl_ratio': 1, 'cvdd_ratio': 1,
        'cipherb': 1, 'mayer_multiple': 1, 'fear_greed': 1,
        'm2_yoy': 1, 'btc_price_cycle': 1,
    }
    assert assess_data_quality(valid)['status'] == 'valid'
    del valid['btc_price_cycle']
    assert assess_data_quality(valid)['status'] == 'degraded'
