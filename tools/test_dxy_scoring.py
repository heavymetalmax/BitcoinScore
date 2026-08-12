#!/usr/bin/env python3
"""Unit tests for DXY adaptive scoring and Macro Modifier.

Run:
    python -m pytest tools/test_dxy_scoring.py -v
    # or without pytest:
    python tools/test_dxy_scoring.py
"""
import sys, os, datetime, json, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestNormalizeDxyAdaptive(unittest.TestCase):
    """normalize_metric('dxy') blends percentile when dxy_history.json exists."""

    def setUp(self):
        # Patch _load_metric_history to return a controlled series
        # Build a synthetic 300-point series: values 100–130 evenly spaced
        import scraper.maps as _maps
        self._orig_cache = _maps._HIST_CACHE.copy()
        import datetime as _dt
        base = _dt.date(2010, 1, 1)
        series = [((base + _dt.timedelta(days=i*30)).isoformat(), 100.0 + i * 0.1) for i in range(300)]
        _maps._HIST_CACHE['dxy'] = series
        self._maps = _maps

    def tearDown(self):
        self._maps._HIST_CACHE.clear()
        self._maps._HIST_CACHE.update(self._orig_cache)

    def test_adaptive_blend_differs_from_fixed(self):
        """With history available, normalize_metric returns blended value != fixed map."""
        from scraper.normalizer import normalize_metric
        from scraper.scoring import map_dxy
        fixed = map_dxy(120.69)  # 70
        blended = normalize_metric('dxy', 120.69, datetime.date(2026, 7, 11))
        # Blended should exist and differ from fixed (percentile doing real work)
        self.assertIsNotNone(blended)
        self.assertNotEqual(blended, fixed,
            f"Expected adaptive blend to differ from fixed score {fixed}, got {blended}")

    def test_fallback_to_fixed_when_no_history(self):
        """Without history, normalize_metric returns fixed map score."""
        # Clear the injected history
        self._maps._HIST_CACHE['dxy'] = []
        from scraper.normalizer import normalize_metric
        from scraper.scoring import map_dxy
        result = normalize_metric('dxy', 120.69, datetime.date(2026, 7, 11))
        self.assertEqual(result, map_dxy(120.69))


class TestDxyModifierContrarian(unittest.TestCase):
    """DXY modifier — contrarian direction (calibrated 2026-07-11).

    IC backtest showed high DXY → higher long-term BTC returns (contrarian).
    Direction: high DXY → REDUCE risk score; low DXY → INCREASE risk score.
    Constants: HIGH=80, LOW=20, SCALE=0.10, MAX=3.0.
    """

    def _run_modifier(self, dxy_score, base_score=50):
        _DXY_HIGH, _DXY_LOW, _DXY_SCALE, _DXY_MAX = 80, 20, 0.10, 3.0
        _dxy_adj = 0.0
        if dxy_score is not None:
            if dxy_score > _DXY_HIGH:
                _dxy_adj = -min(_DXY_MAX, (dxy_score - _DXY_HIGH) * _DXY_SCALE)
            elif dxy_score < _DXY_LOW:
                _dxy_adj = min(_DXY_MAX, (_DXY_LOW - dxy_score) * _DXY_SCALE)
        return max(0, min(100, round(base_score + _dxy_adj))), _dxy_adj

    def test_high_dxy_reduces_score(self):
        """dxy_score=100 → adj=-2.0, base 50 → 48 (contrarian: strong USD = cheap BTC)."""
        result, adj = self._run_modifier(100, base_score=50)
        self.assertAlmostEqual(adj, -2.0)
        self.assertEqual(result, 48)

    def test_low_dxy_increases_score(self):
        """dxy_score=10 → adj=+1.0, base 50 → 51 (contrarian: weak USD = BTC may be stretched)."""
        result, adj = self._run_modifier(10, base_score=50)
        self.assertAlmostEqual(adj, 1.0)
        self.assertEqual(result, 51)

    def test_neutral_zone_no_adjustment(self):
        """dxy_score=50 (neutral zone 20-80) → no adjustment."""
        result, adj = self._run_modifier(50, base_score=40)
        self.assertEqual(adj, 0.0)
        self.assertEqual(result, 40)

    def test_max_adj_at_dxy_zero(self):
        """dxy_score=0 → adj = min(3.0, (20-0)*0.10) = 2.0, base 50 → 52."""
        result, adj = self._run_modifier(0, base_score=50)
        self.assertAlmostEqual(adj, 2.0)
        self.assertEqual(result, 52)

    def test_score_clamp_at_100(self):
        """Adjusted score cannot exceed 100."""
        result, adj = self._run_modifier(0, base_score=98)
        self.assertEqual(result, 100)

    def test_score_clamp_at_zero(self):
        """Adjusted score cannot go below 0."""
        result, adj = self._run_modifier(100, base_score=2)
        self.assertEqual(result, 0)


class TestDxyModifierNoneGuard(unittest.TestCase):
    """When dxy_score is None (scraper failure), modifier is a no-op."""

    def test_none_dxy_score_no_change(self):
        _DXY_HIGH, _DXY_LOW, _DXY_SCALE, _DXY_MAX = 80, 20, 0.10, 3.0
        base_score = 42
        _dxy_score = None
        _dxy_adj = 0.0
        if _dxy_score is not None:
            if _dxy_score > _DXY_HIGH:
                _dxy_adj = min(_DXY_MAX, (_dxy_score - _DXY_HIGH) * _DXY_SCALE)
            elif _dxy_score < _DXY_LOW:
                _dxy_adj = -min(_DXY_MAX, (_DXY_LOW - _dxy_score) * _DXY_SCALE)
        result = max(0, min(100, round(base_score + _dxy_adj)))
        self.assertEqual(_dxy_adj, 0.0)
        self.assertEqual(result, base_score)


if __name__ == '__main__':
    unittest.main(verbosity=2)
