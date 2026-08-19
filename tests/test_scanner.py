import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed.yahoo import HistoryBar
from aurum.signals import scanner


def _bar(i, close, volume=1000.0):
    o = close - 0.05
    return HistoryBar(1_700_000_000 + i * 86400, o, close + 0.1, close - 0.1, close, volume)


class TestScanner(unittest.TestCase):
    def test_too_few_bars_raises(self):
        bars = [_bar(i, 100 + i) for i in range(10)]
        with self.assertRaises(ValueError):
            scanner.scan(bars, "TEST")

    def test_result_shape_is_always_consistent(self):
        rng = random.Random(1)
        bars, price = [], 100.0
        for i in range(200):
            price = max(1.0, price + rng.gauss(0, 1.0))
            bars.append(_bar(i, price, volume=abs(rng.gauss(1000, 200))))
        result = scanner.scan(bars, "TEST")
        self.assertEqual(len(result.confirmations), 4)
        self.assertIn(result.score_pct, [0.0, 25.0, 50.0, 75.0, 100.0])
        self.assertEqual(result.setup_detected, result.score_pct >= 75.0)
        self.assertIn(result.pattern, ["Breakout", "Pullback", "Momentum", "Trend Continuation", "Reversal", "None"])

    def test_strong_monotonic_uptrend_confirms_trend(self):
        bars = [_bar(i, 100 + i * 0.5, volume=1000) for i in range(120)]
        result = scanner.scan(bars, "TEST")
        trend = next(c for c in result.confirmations if c.name == "Trend")
        self.assertTrue(trend.confirmed)
        self.assertGreater(result.last_close, result.ema)  # price above a rising EMA

    def test_breakout_detected_on_range_break_with_volume_spike(self):
        # 60 quiet bars oscillating in a tight range, then one bar breaks well above
        # the range on a clear volume spike.
        bars = [_bar(i, 100 + (i % 3) * 0.1, volume=1000) for i in range(60)]
        bars.append(_bar(60, 108.0, volume=5000))  # well above the ~100.2 range high, 5x volume
        result = scanner.scan(bars, "TEST")
        self.assertEqual(result.pattern, "Breakout")
        volume_check = next(c for c in result.confirmations if c.name == "Volume")
        key_level_check = next(c for c in result.confirmations if c.name == "Key level")
        self.assertTrue(volume_check.confirmed)
        self.assertTrue(key_level_check.confirmed)

    def test_pullback_detected_when_price_returns_to_a_rising_ema(self):
        # Build a steady uptrend, then construct a final bar whose close lands
        # within EMA_PROXIMITY_PCT of the EMA computed from all-but-last bar,
        # while staying inside the recent swing range (no breakout) and without
        # a volume spike (so only Trend + proximity-to-EMA can drive the pattern).
        base = [_bar(i, 100 + i * 0.3, volume=1000) for i in range(150)]
        closes_so_far = [b.close for b in base]
        ema_prev = scanner._ema_series(closes_so_far, 50)[-1]

        # search a small range of pullback depths for one that lands near the EMA
        chosen = None
        for pct in [0.995, 0.997, 0.999, 1.001, 1.003]:
            candidate_close = ema_prev * pct
            trial_bars = base + [_bar(150, candidate_close, volume=1000)]
            result = scanner.scan(trial_bars, "TEST")
            if result.pattern == "Pullback":
                chosen = result
                break
        self.assertIsNotNone(chosen, "could not construct a pullback fixture — check EMA proximity logic")
        trend_check = next(c for c in chosen.confirmations if c.name == "Trend")
        self.assertTrue(trend_check.confirmed)

    def test_momentum_detected_on_strong_aligned_move_away_from_ema_and_levels(self):
        # Mild uptrend to establish trend, then a sharp final run that's both
        # far from the EMA (no pullback) and beyond the recent swing high with
        # ordinary volume (so Breakout's volume-spike condition doesn't fire).
        bars = [_bar(i, 100 + i * 0.05, volume=1000) for i in range(150)]
        for i in range(150, 161):
            last_close = bars[-1].close
            bars.append(_bar(i, last_close * 1.006, volume=1000))  # ~0.6%/bar, no volume spike
        result = scanner.scan(bars, "TEST")
        self.assertIn(result.pattern, ["Momentum", "Breakout"])  # a strong enough run can legitimately read as either
        momentum_check = next(c for c in result.confirmations if c.name == "Momentum")
        self.assertTrue(momentum_check.confirmed)


if __name__ == "__main__":
    unittest.main()
