#!/usr/bin/env python3
"""
Smoke / regression tests for the BitcoinScore pipeline.

Run:
    python -m pytest tools/test_pipeline_smoke.py -v
    # from project root (BitcoinScore/)
"""

import os
import sys
import json
import math

import pytest

# Ensure project root is on the path so "scraper" package is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ── Scoring function boundary tests ────────────────────────────────────────

class TestMapEtfFlow:
    """map_etf_flow: ≤ -1000 → 0, = 375 → 50, ≥ 2000 → 100."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from scraper.scoring import map_etf_flow
        self.fn = map_etf_flow

    def test_min_boundary(self):
        assert self.fn(-1000) == 0

    def test_below_min(self):
        assert self.fn(-2000) == 0

    def test_mid(self):
        assert self.fn(375) == 50

    def test_max_boundary(self):
        assert self.fn(2000) == 100

    def test_above_max(self):
        assert self.fn(5000) == 100

    def test_none(self):
        assert self.fn(None) is None

    def test_dict_value(self):
        # dict with 'value' key — used when etf_flows is stored as metric object
        assert self.fn({'value': 2000}) == 100

    def test_midrange(self):
        result = self.fn(0)
        assert 20 < result < 40  # 0 sits between min(-1000) and mid(375)


class TestMapYieldCurve:
    """map_yield_curve: ≤ -1.0 → 100, ≥ +2.0 → 0, 0.5 → ~50."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from scraper.scoring import map_yield_curve
        self.fn = map_yield_curve

    def test_deep_inversion(self):
        assert self.fn(-1.0) == 100

    def test_steep_curve(self):
        assert self.fn(2.0) == 0

    def test_none(self):
        assert self.fn(None) is None

    def test_mid(self):
        # 0.5 → (2.0 - 0.5) / 3.0 * 100 = 50
        assert self.fn(0.5) == 50

    def test_clamped_above(self):
        assert self.fn(3.0) == 0

    def test_clamped_below(self):
        assert self.fn(-2.0) == 100


class TestMapNupl:
    """map_nupl: None → None, very low → low score, very high → high score."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from scraper.scoring import map_nupl
        self.fn = map_nupl

    def test_none(self):
        assert self.fn(None) is None

    def test_bottom_territory(self):
        # Extreme negative (capitulation) → very low score (0–20)
        score = self.fn(-30)
        assert 0 <= score <= 20, f'Expected low score for NUPL=-30, got {score}'

    def test_max_euphoria(self):
        # NUPL ~80-100 → top territory → high score
        score = self.fn(80)
        assert score > 80, f'Expected high score for NUPL=80, got {score}'

    def test_neutral(self):
        # NUPL=40 is the boundary → score ~50
        score = self.fn(40)
        assert 45 <= score <= 55, f'Expected ~50 for NUPL=40, got {score}'

    def test_range_0_to_100(self):
        for v in [-50, -20, 0, 20, 40, 60, 75, 100]:
            s = self.fn(v)
            assert s is not None
            assert 0 <= s <= 100, f'map_nupl({v})={s} out of range'


class TestMapMvrv:
    """map_mvrv: Z-score mapping."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from scraper.scoring import map_mvrv
        self.fn = map_mvrv

    def test_none(self):
        assert self.fn(None) is None

    def test_low_z(self):
        # Z < 0 is deeply undervalued → score near 0
        score = self.fn(-0.3)
        assert 0 <= score <= 20, f'Expected low score for MVRV=-0.3, got {score}'

    def test_high_z(self):
        # Z = 5 (peak euphoria) → score ~100
        score = self.fn(5.0)
        assert score >= 95, f'Expected near-100 for MVRV=5.0, got {score}'

    def test_neutral(self):
        # Z = 1.0 is boundary → score = 50
        score = self.fn(1.0)
        assert 45 <= score <= 55, f'Expected ~50 for MVRV=1.0, got {score}'

    def test_range(self):
        for v in [-0.5, 0, 1.0, 2.0, 3.5, 5.0]:
            s = self.fn(v)
            assert s is not None
            assert 0 <= s <= 100, f'map_mvrv({v})={s} out of range'


# ── load_relevance_weights ─────────────────────────────────────────────────

class TestLoadRelevanceWeights:
    """load_relevance_weights() must produce non-empty RELEVANCE_PROFILES."""

    def test_profiles_non_empty(self):
        from scraper.utility_evaluator import load_relevance_weights, RELEVANCE_PROFILES
        load_relevance_weights()
        assert isinstance(RELEVANCE_PROFILES, dict), 'RELEVANCE_PROFILES must be a dict'
        assert len(RELEVANCE_PROFILES) > 0, 'RELEVANCE_PROFILES must not be empty after load'

    def test_profiles_have_valid_structure(self):
        from scraper.utility_evaluator import RELEVANCE_PROFILES
        for metric, profile in RELEVANCE_PROFILES.items():
            assert isinstance(profile, dict), f'{metric} profile must be a dict'
            for phase in ('BOTTOM', 'NEUTRAL', 'TOP'):
                assert phase in profile, f'{metric} missing phase {phase}'
                w = profile[phase]
                assert isinstance(w, (int, float)), f'{metric}.{phase} weight must be numeric'
                assert 0.0 <= w <= 1.0, f'{metric}.{phase}={w} out of [0,1]'


# ── HMM model ──────────────────────────────────────────────────────────────

class TestHmmModel:
    """data/v3_phase_model.pkl: loadable, predict_proba sums to ~1.0."""

    MODEL_PATH = os.path.join(PROJECT_ROOT, 'data', 'v3_phase_model.pkl')

    def test_model_file_exists(self):
        assert os.path.exists(self.MODEL_PATH), (
            f'HMM model not found at {self.MODEL_PATH}'
        )

    def _load_model(self):
        """Load the HMM pickle, remapping HMMPhaseClassifier if needed.
        Skips gracefully when sklearn is not installed."""
        import pickle

        try:
            import sklearn  # noqa: F401  — just to check availability
        except ImportError:
            pytest.skip('sklearn not installed — skipping HMM model tests')

        try:
            sys.path.insert(0, os.path.join(PROJECT_ROOT, 'tools'))
            from train_v3_hmm_model import HMMPhaseClassifier as _HMM
        except Exception:
            _HMM = None

        class _Unpickler(pickle.Unpickler):
            def find_class(self, module, name):
                if name == 'HMMPhaseClassifier' and _HMM is not None:
                    return _HMM
                return super().find_class(module, name)

        with open(self.MODEL_PATH, 'rb') as f:
            return _Unpickler(f).load()

    def test_model_loads(self):
        model_data = self._load_model()
        assert model_data is not None, 'model_data must not be None'

    def test_predict_proba_sums_to_one(self):
        """HMM classifier predict_proba for a neutral feature vector sums to ~1.0."""
        model_data = self._load_model()

        # The model object may be stored under various keys
        classifier = None
        if hasattr(model_data, 'predict_proba'):
            classifier = model_data
        elif isinstance(model_data, dict):
            for key in ('model', 'classifier', 'hmm'):
                if hasattr(model_data.get(key), 'predict_proba'):
                    classifier = model_data[key]
                    break

        if classifier is None:
            pytest.skip(
                'Model object does not expose predict_proba — '
                'skipping probabilities test (model may use internal phase logic)'
            )

        # neutral feature vector: all scores = 50 out of 100
        feature_vector = [[50] * 10]
        proba = classifier.predict_proba(feature_vector)
        total = sum(proba[0])
        assert abs(total - 1.0) < 0.01, f'predict_proba sums to {total}, expected ~1.0'


# ── sparklines.json data quality ─────────────────────────────────────────

class TestSparklinesQuality:
    """web/sparklines.json must have key metrics with >50 non-null values."""

    SPARKLINES_PATH = os.path.join(PROJECT_ROOT, 'web', 'sparklines.json')
    MIN_NON_NULL = 50

    REQUIRED_KEYS = ['nupl', 'mvrv_z_score', 'etf_flows', 'fear_greed']

    def test_sparklines_exists(self):
        assert os.path.exists(self.SPARKLINES_PATH), (
            f'sparklines.json not found at {self.SPARKLINES_PATH}'
        )

    def test_sparklines_loads(self):
        with open(self.SPARKLINES_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict), 'sparklines.json must be a JSON object'

    def test_required_keys_present(self):
        with open(self.SPARKLINES_PATH) as f:
            data = json.load(f)
        for key in self.REQUIRED_KEYS:
            assert key in data, f'sparklines.json missing key {key!r}'

    @pytest.mark.parametrize('key', ['nupl', 'mvrv_z_score', 'etf_flows', 'fear_greed'])
    def test_key_has_sufficient_non_null(self, key):
        with open(self.SPARKLINES_PATH) as f:
            data = json.load(f)
        if key not in data:
            pytest.skip(f'{key} not present in sparklines.json')
        vals = data[key]
        non_null = sum(1 for v in vals if v is not None)
        assert non_null >= self.MIN_NON_NULL, (
            f'sparklines.json[{key!r}]: only {non_null} non-null values, '
            f'expected >={self.MIN_NON_NULL}'
        )


class TestBottomConfirmationFactor:
    """bottom_confirmation_factor: decay multiplier for unconfirmed labeled bottoms."""

    @pytest.fixture(autouse=True)
    def _import(self):
        import datetime as dt
        from scraper.scoring_v3 import bottom_confirmation_factor
        self.fn = bottom_confirmation_factor
        self.dt = dt
        # Use the most recent labeled bottom: 2026-06-04 at $62,951
        self.bottom_date = dt.date(2026, 6, 4)
        self.bottom_price = 62951.0

    def test_day_zero_no_recovery(self):
        cf = self.fn(self.bottom_date, self.bottom_price)
        assert cf == pytest.approx(0.5, abs=1e-6)

    def test_90_days_no_recovery(self):
        target = self.bottom_date + self.dt.timedelta(days=90)
        cf = self.fn(target, self.bottom_price)
        assert cf == pytest.approx(0.75, abs=1e-4)

    def test_180_days_confirmed_by_time(self):
        target = self.bottom_date + self.dt.timedelta(days=180)
        cf = self.fn(target, self.bottom_price)
        assert cf == pytest.approx(1.0, abs=1e-6)

    def test_beyond_180_days(self):
        target = self.bottom_date + self.dt.timedelta(days=300)
        cf = self.fn(target, self.bottom_price)
        assert cf == 1.0

    def test_30pct_recovery_confirmed(self):
        recovered_price = self.bottom_price * 1.30
        cf = self.fn(self.bottom_date, recovered_price)
        assert cf == 1.0

    def test_negative_recovery_clamped_to_floor(self):
        fallen_price = self.bottom_price * 0.80  # price fell 20% below bottom
        cf_fallen = self.fn(self.bottom_date, fallen_price)
        cf_zero   = self.fn(self.bottom_date, self.bottom_price)
        assert cf_fallen == pytest.approx(cf_zero, abs=1e-6)

    def test_none_price_returns_half(self):
        cf = self.fn(self.bottom_date, None)
        assert cf == 0.5

    def test_zero_price_returns_half(self):
        cf = self.fn(self.bottom_date, 0.0)
        assert cf == 0.5

    def test_pre_any_bottom_returns_one(self):
        pre = self.dt.date(2015, 1, 1)
        cf = self.fn(pre, 300.0)
        assert cf == 1.0

    def test_confirmed_historical_bottom_returns_one(self):
        # 2018-12-15 bottom is >180 days old by 2019-07-01
        target = self.dt.date(2019, 7, 1)
        cf = self.fn(target, 11000.0)
        assert cf == 1.0


if __name__ == '__main__':
    # Allow running without pytest
    import unittest
    pytest.main([__file__, '-v'])
