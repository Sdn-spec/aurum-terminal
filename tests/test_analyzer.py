import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed.yahoo import HistoryBar
from aurum.report import analyzer
from aurum.signals.scanner import Confirmation, ScanResult


def _trending_bars(n=260, start=100.0, step=0.3, seed=1, volume=1000.0):
    rng = random.Random(seed)
    bars, price, ts = [], start, 1_700_000_000
    for _ in range(n):
        price = price + step + rng.gauss(0, 0.05)
        o = price - 0.05
        bars.append(HistoryBar(ts, o, price + 0.1, price - 0.1, price, volume))
        ts += 86400
    return bars


def _flat_bars(n=260, start=100.0, seed=1, volume=1000.0):
    rng = random.Random(seed)
    bars, ts = [], 1_700_000_000
    for _ in range(n):
        price = start + rng.gauss(0, 0.05)
        o = price - 0.02
        bars.append(HistoryBar(ts, o, price + 0.05, price - 0.05, price, volume))
        ts += 86400
    return bars


class TestAnalyzeSymbol(unittest.TestCase):
    def test_runs_end_to_end_on_an_uptrend(self):
        bars = _trending_bars(step=0.3)
        report = analyzer.analyze_symbol("TEST", bars, equity=3000.0, peak_equity=3000.0)
        self.assertEqual(report.symbol, "TEST")
        self.assertEqual(report.research.trend_regime, "Uptrend")
        self.assertEqual(report.long_term.direction, "long")
        self.assertIn(report.verdict, ["INVEST", "WATCH", "AVOID"])
        self.assertIn(report.confidence, ["High", "Medium", "Low"])

    def test_downtrend_flips_long_term_direction_to_short(self):
        bars = _trending_bars(step=-0.3)
        report = analyzer.analyze_symbol("TEST", bars, equity=3000.0, peak_equity=3000.0)
        self.assertEqual(report.research.trend_regime, "Downtrend")
        self.assertEqual(report.long_term.direction, "short")
        self.assertGreater(report.long_term.stop, report.long_term.entry)

    def test_too_little_history_raises_value_error(self):
        bars = _trending_bars(n=50)
        with self.assertRaises(ValueError):
            analyzer.analyze_symbol("TEST", bars, equity=3000.0, peak_equity=3000.0)

    def test_tiny_equity_fails_risk_gate_and_forces_avoid(self):
        bars = _trending_bars(step=0.3)
        # A risk budget of 1% of $1 is essentially nothing, but the position
        # sizing math still produces a real notional exposure well beyond
        # what such a tiny account could support relative to peak equity.
        report = analyzer.analyze_symbol("TEST", bars, equity=1.0, peak_equity=100000.0)
        self.assertFalse(report.risk.passed)
        self.assertEqual(report.verdict, "AVOID")

    def test_macro_news_earnings_default_to_empty_when_not_supplied(self):
        bars = _trending_bars(step=0.3)
        report = analyzer.analyze_symbol("TEST", bars, equity=3000.0, peak_equity=3000.0)
        self.assertEqual(report.macro, [])
        self.assertEqual(report.news, [])
        self.assertIsNone(report.earnings)

    def test_macro_news_earnings_pass_through_unchanged_when_supplied(self):
        bars = _trending_bars(step=0.3)
        macro = [{"key": "fed_funds_rate", "latest_value": 4.33}]
        news = [{"headline": "Something happened"}]
        earnings = {"date": "2026-11-05", "eps_estimate": 1.42}
        report = analyzer.analyze_symbol(
            "TEST", bars, equity=3000.0, peak_equity=3000.0, macro=macro, news=news, earnings=earnings
        )
        self.assertEqual(report.macro, macro)
        self.assertEqual(report.news, news)
        self.assertEqual(report.earnings, earnings)
        # informational only -- passing macro/news/earnings must not change the score
        baseline = analyzer.analyze_symbol("TEST", bars, equity=3000.0, peak_equity=3000.0)
        self.assertEqual(report.score, baseline.score)
        self.assertEqual(report.verdict, baseline.verdict)

    def test_take_profit_2_is_further_than_take_profit_1_in_plan_direction(self):
        bars = _trending_bars(step=0.3)
        report = analyzer.analyze_symbol("TEST", bars, equity=3000.0, peak_equity=3000.0)
        if report.day_trade.direction == "long":
            self.assertGreater(report.day_trade.take_profit_2, report.day_trade.take_profit_1)
        else:
            self.assertLess(report.day_trade.take_profit_2, report.day_trade.take_profit_1)


class TestBuildDebate(unittest.TestCase):
    def _scan(self, trend=True, momentum=True, volume=True, key_level=True, last_close=110.0, ema=100.0):
        return ScanResult(
            symbol="TEST",
            pattern="Pullback",
            confirmations=[
                Confirmation("Trend", trend, "50-EMA rising, price above it"),
                Confirmation("Momentum", momentum, "+2.00% over the last 10 bars"),
                Confirmation("Volume", volume, "last bar 1200 vs 1.2x avg 900"),
                Confirmation("Key level", key_level, "range detail"),
            ],
            score_pct=100.0 if all([trend, momentum, volume, key_level]) else 50.0,
            setup_detected=all([trend, momentum, volume, key_level]),
            last_close=last_close,
            ema=ema,
        )

    def _research(self, trend_regime="Uptrend"):
        return analyzer.ResearchSummary(
            trend_regime=trend_regime,
            short_term_ema=100.0,
            long_term_ema=95.0,
            momentum_pct=2.0,
            volatility_annualized_pct=20.0,
            year_high=115.0,
            year_low=90.0,
            distance_from_year_high_pct=-4.3,
            distance_from_year_low_pct=22.2,
        )

    def test_all_bullish_signals_produce_more_bull_points_than_bear(self):
        scan = self._scan()
        research = self._research("Uptrend")
        day_trade = analyzer._make_horizon_plan("day_trade", "long", 110.0, 105.0, 1.5, 3.0, "", [])
        debate = analyzer.build_debate(scan, research, day_trade)
        self.assertGreater(len(debate.bull_points), len(debate.bear_points))

    def test_all_bearish_signals_produce_more_bear_points_than_bull(self):
        scan = self._scan(trend=False, momentum=False, volume=False, key_level=False, last_close=90.0, ema=100.0)
        research = self._research("Downtrend")
        day_trade = analyzer._make_horizon_plan("day_trade", "short", 90.0, 100.0, 1.5, 3.0, "", [])
        debate = analyzer.build_debate(scan, research, day_trade)
        self.assertGreater(len(debate.bear_points), len(debate.bull_points))


class TestMeetsMinRR(unittest.TestCase):
    def test_a_ratio_that_lands_a_hair_under_1_5_from_float_noise_still_counts(self):
        # Reproduces what happened live against real Gold data: entry ~4508 with a
        # small risk_per_unit means tp1 - entry doesn't recover exactly risk * 1.5
        # (catastrophic cancellation on large floats), so the ratio comes back as
        # something like 1.4999999999998 instead of exactly 1.5.
        entry = 4508.32174
        risk_per_unit = 3.41
        tp1 = entry + risk_per_unit * 1.5
        ratio = (tp1 - entry) / risk_per_unit
        self.assertNotEqual(ratio, 1.5)  # confirms the float noise is real, not a hypothetical
        self.assertTrue(analyzer._meets_min_rr(ratio))

    def test_a_genuinely_poor_ratio_still_fails(self):
        self.assertFalse(analyzer._meets_min_rr(1.2))

    def test_day_trade_plan_at_its_own_tp1_multiple_never_contradicts_itself(self):
        # day_trade.take_profit_1 is built at exactly DAY_TRADE_TP1_MULTIPLE (1.5),
        # so build_debate must never claim it's "below the 1.5:1 minimum."
        day_trade = analyzer._make_horizon_plan(
            "day_trade", "long", 4508.32174, 4504.91174,
            analyzer.DAY_TRADE_TP1_MULTIPLE, analyzer.DAY_TRADE_TP2_MULTIPLE, "", [],
        )
        scan = ScanResult(
            symbol="TEST", pattern="Pullback",
            confirmations=[Confirmation("Trend", True, ""), Confirmation("Momentum", True, ""),
                           Confirmation("Volume", True, ""), Confirmation("Key level", True, "")],
            score_pct=100.0, setup_detected=True, last_close=4508.32174, ema=4400.0,
        )
        research = analyzer.ResearchSummary(
            trend_regime="Uptrend", short_term_ema=4400.0, long_term_ema=4300.0, momentum_pct=2.0,
            volatility_annualized_pct=20.0, year_high=4600.0, year_low=4000.0,
            distance_from_year_high_pct=-2.0, distance_from_year_low_pct=12.0,
        )
        debate = analyzer.build_debate(scan, research, day_trade)
        self.assertFalse(any("below the" in p for p in debate.bear_points))


if __name__ == "__main__":
    unittest.main()
