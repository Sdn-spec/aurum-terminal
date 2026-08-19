import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.decision import memo
from aurum.risk import engine as risk_engine
from aurum.signals.scanner import Confirmation, ScanResult


def _scan(setup_detected: bool, score_pct: float, last_close: float = 110.0, ema: float = 100.0) -> ScanResult:
    return ScanResult(
        symbol="TEST",
        pattern="Pullback",
        confirmations=[Confirmation("Trend", True, "")] * 4,
        score_pct=score_pct,
        setup_detected=setup_detected,
        last_close=last_close,
        ema=ema,
    )


def _passing_risk() -> risk_engine.RiskAssessment:
    return risk_engine.assess_trade(equity=3000.0, peak_equity=3000.0, proposed_risk_dollars=30.0, proposed_notional=300.0)


def _failing_risk() -> risk_engine.RiskAssessment:
    return risk_engine.assess_trade(equity=3000.0, peak_equity=3000.0, proposed_risk_dollars=500.0, proposed_notional=300.0)


class TestTradePlan(unittest.TestCase):
    def test_long_plan_when_price_above_ema(self):
        scan = _scan(True, 100.0, last_close=110.0, ema=100.0)
        plan = memo.build_trade_plan(scan, swing_low=105.0, swing_high=112.0)
        self.assertEqual(plan.direction, "long")
        self.assertLess(plan.stop, plan.entry)
        self.assertGreater(plan.target, plan.entry)

    def test_short_plan_when_price_below_ema(self):
        scan = _scan(True, 100.0, last_close=90.0, ema=100.0)
        plan = memo.build_trade_plan(scan, swing_low=85.0, swing_high=95.0)
        self.assertEqual(plan.direction, "short")
        self.assertGreater(plan.stop, plan.entry)
        self.assertLess(plan.target, plan.entry)

    def test_risk_reward_ratio_matches_reward_multiple(self):
        scan = _scan(True, 100.0, last_close=110.0, ema=100.0)
        plan = memo.build_trade_plan(scan, swing_low=105.0, swing_high=112.0, stop_buffer_pct=0.0, reward_multiple=3.0)
        self.assertAlmostEqual(plan.risk_reward_ratio, 3.0, places=4)


class TestDecisionMemo(unittest.TestCase):
    def test_good_setup_and_passing_risk_and_good_rr_gets_approved(self):
        scan = _scan(setup_detected=True, score_pct=100.0)
        plan = memo.TradePlan(direction="long", entry=110.0, stop=105.0, target=120.0)  # RR = 2.0
        result = memo.build_memo("TEST", scan, _passing_risk(), plan)
        self.assertEqual(result.verdict, "APPROVED")
        self.assertEqual(result.reasons, [])

    def test_failing_risk_gate_always_rejects_even_with_a_great_setup(self):
        scan = _scan(setup_detected=True, score_pct=100.0)
        plan = memo.TradePlan(direction="long", entry=110.0, stop=105.0, target=130.0)  # RR = 4.0
        result = memo.build_memo("TEST", scan, _failing_risk(), plan)
        self.assertEqual(result.verdict, "REJECTED")
        self.assertTrue(any("Risk gate failed" in r for r in result.reasons))

    def test_weak_setup_with_passing_risk_goes_to_watchlist_not_rejected(self):
        scan = _scan(setup_detected=False, score_pct=50.0)
        plan = memo.TradePlan(direction="long", entry=110.0, stop=105.0, target=120.0)  # RR = 2.0, fine
        result = memo.build_memo("TEST", scan, _passing_risk(), plan)
        self.assertEqual(result.verdict, "WATCHLIST")
        self.assertTrue(any("Setup score" in r for r in result.reasons))

    def test_poor_risk_reward_prevents_approval_even_with_good_setup(self):
        scan = _scan(setup_detected=True, score_pct=100.0)
        plan = memo.TradePlan(direction="long", entry=110.0, stop=100.0, target=115.0)  # RR = 0.5
        result = memo.build_memo("TEST", scan, _passing_risk(), plan)
        self.assertEqual(result.verdict, "WATCHLIST")
        self.assertTrue(any("Risk/reward" in r for r in result.reasons))


if __name__ == "__main__":
    unittest.main()
