import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.risk import engine


class TestRiskEngine(unittest.TestCase):
    def test_all_checks_pass_within_default_limits(self):
        result = engine.assess_trade(
            equity=3000.0,
            peak_equity=3000.0,
            proposed_risk_dollars=30.0,  # 1% of equity
            proposed_notional=300.0,
            current_open_notional=0.0,
            realized_pnl_today=0.0,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.status, "PROTECTED")
        self.assertEqual(len(result.checks), 5)

    def test_oversized_risk_fails_position_size_check_only(self):
        result = engine.assess_trade(
            equity=3000.0,
            peak_equity=3000.0,
            proposed_risk_dollars=150.0,  # 5% of equity, default limit is 1%
            proposed_notional=300.0,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.status, "BLOCKED")
        position_check = next(c for c in result.checks if c.name == "Position size")
        self.assertFalse(position_check.passed)
        exposure_check = next(c for c in result.checks if c.name == "Exposure limit")
        self.assertTrue(exposure_check.passed)

    def test_drawdown_beyond_limit_blocks(self):
        result = engine.assess_trade(
            equity=2000.0,
            peak_equity=3000.0,  # 33% drawdown, default limit is 20%
            proposed_risk_dollars=10.0,
            proposed_notional=100.0,
        )
        drawdown_check = next(c for c in result.checks if c.name == "Drawdown")
        self.assertFalse(drawdown_check.passed)
        self.assertFalse(result.passed)

    def test_daily_loss_limit_blocks_further_risk(self):
        result = engine.assess_trade(
            equity=3000.0,
            peak_equity=3000.0,
            proposed_risk_dollars=10.0,
            proposed_notional=100.0,
            realized_pnl_today=-100.0,  # -3.3%, default limit is 2%
        )
        loss_check = next(c for c in result.checks if c.name == "Max daily loss")
        self.assertFalse(loss_check.passed)
        self.assertFalse(result.passed)

    def test_positive_pnl_today_does_not_count_against_daily_loss(self):
        result = engine.assess_trade(
            equity=3000.0, peak_equity=3000.0, proposed_risk_dollars=10.0,
            proposed_notional=100.0, realized_pnl_today=500.0,
        )
        loss_check = next(c for c in result.checks if c.name == "Max daily loss")
        self.assertTrue(loss_check.passed)
        self.assertEqual(result.daily_loss_used_pct, 0.0)

    def test_volatility_check_skipped_without_data(self):
        result = engine.assess_trade(
            equity=3000.0, peak_equity=3000.0, proposed_risk_dollars=10.0, proposed_notional=100.0
        )
        vol_check = next(c for c in result.checks if c.name == "Volatility")
        self.assertTrue(vol_check.passed)
        self.assertIn("Skipped", vol_check.detail)

    def test_high_volatility_fails_when_data_supplied(self):
        wild_returns = [0.15, -0.18, 0.20, -0.22, 0.19, -0.21, 0.17]  # absurdly volatile
        result = engine.assess_trade(
            equity=3000.0, peak_equity=3000.0, proposed_risk_dollars=10.0,
            proposed_notional=100.0, recent_returns=wild_returns,
        )
        vol_check = next(c for c in result.checks if c.name == "Volatility")
        self.assertFalse(vol_check.passed)

    def test_custom_limits_are_respected(self):
        tight_limits = engine.RiskLimits(max_risk_per_trade_pct=0.1)
        result = engine.assess_trade(
            equity=3000.0, peak_equity=3000.0, proposed_risk_dollars=30.0,  # 1%, fine by default but not by tight_limits
            proposed_notional=300.0, limits=tight_limits,
        )
        position_check = next(c for c in result.checks if c.name == "Position size")
        self.assertFalse(position_check.passed)


if __name__ == "__main__":
    unittest.main()
