import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed.yahoo import HistoryBar
from aurum.optimize import engine


def _synthetic_returns(seed=42, n_days=400, assets=("A", "B", "C")):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({a: rng.normal(0.0003, 0.01, n_days) for a in assets})


class TestOptimizeEngine(unittest.TestCase):
    def test_weights_sum_to_one_and_are_long_only(self):
        returns = _synthetic_returns()
        result = engine.optimize(returns, method="mean_risk")
        self.assertAlmostEqual(sum(result.weights.values()), 1.0, places=6)
        for w in result.weights.values():
            self.assertGreaterEqual(w, -1e-9)

    def test_hrp_also_produces_valid_weights(self):
        returns = _synthetic_returns()
        result = engine.optimize(returns, method="hrp")
        self.assertAlmostEqual(sum(result.weights.values()), 1.0, places=6)
        self.assertTrue(result.method.startswith("skfolio"))

    def test_fallback_path_used_when_skfolio_unavailable(self):
        returns = _synthetic_returns()
        original = engine._SKFOLIO_AVAILABLE
        engine._SKFOLIO_AVAILABLE = False
        try:
            result = engine.optimize(returns, method="mean_risk")
        finally:
            engine._SKFOLIO_AVAILABLE = original
        self.assertEqual(result.method, "fallback:mean_variance")
        self.assertAlmostEqual(sum(result.weights.values()), 1.0, places=6)

    def test_returns_from_bars_aligns_on_shared_timestamps(self):
        bars_a = [HistoryBar(1000 + i * 86400, 1, 1, 1, 100 + i, 0) for i in range(5)]
        bars_b = [HistoryBar(1000 + i * 86400, 1, 1, 1, 50 + i, 0) for i in range(3, 8)]  # partial overlap
        returns = engine.returns_from_bars({"A": bars_a, "B": bars_b})
        # only overlapping timestamps (i=3,4 -> 2 prices -> 1 return) survive the inner join + pct_change
        self.assertEqual(len(returns), 1)
        self.assertListEqual(list(returns.columns), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
