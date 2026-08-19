import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.forecast import baseline


class TestBaselineForecast(unittest.TestCase):
    def test_flat_series_forecasts_flat_with_zero_band(self):
        closes = [100.0] * 30
        result = baseline.forecast(closes, horizon=5)
        self.assertEqual(result.horizon, 5)
        for p in result.point_forecast:
            self.assertAlmostEqual(p, 100.0, places=6)
        for lo, hi in zip(result.lower_band, result.upper_band):
            self.assertAlmostEqual(lo, hi, places=6)  # zero volatility -> zero-width band

    def test_steady_uptrend_extrapolates_upward(self):
        closes = [100.0 * (1.001**i) for i in range(60)]
        result = baseline.forecast(closes, horizon=10)
        self.assertGreater(result.point_forecast[-1], closes[-1])
        # monotonically increasing point forecast for a steady positive drift
        self.assertEqual(result.point_forecast, sorted(result.point_forecast))

    def test_band_widens_with_horizon(self):
        closes = [100.0 + (i % 7) * 0.5 for i in range(50)]  # some noise, no strong trend
        result = baseline.forecast(closes, horizon=10)
        widths = [hi - lo for lo, hi in zip(result.lower_band, result.upper_band)]
        for earlier, later in zip(widths, widths[1:]):
            self.assertGreaterEqual(later, earlier)  # sqrt(h) growth -> non-decreasing

    def test_too_few_closes_raises(self):
        with self.assertRaises(ValueError):
            baseline.forecast([1.0, 2.0, 3.0], horizon=5)


if __name__ == "__main__":
    unittest.main()
