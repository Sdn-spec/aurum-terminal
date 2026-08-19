"""The risk gate every proposed trade passes through before it's shown as
approvable — mirrors a real risk desk's checklist: position size, exposure,
drawdown, volatility, daily loss. Any single failed check blocks the trade;
this module only ever answers "would this be reckless," it never places
an order itself.
"""

import math
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RiskLimits:
    max_risk_per_trade_pct: float = 1.0        # % of equity risked on one trade
    max_portfolio_exposure_pct: float = 50.0    # % of equity allowed as open notional
    max_drawdown_pct: float = 20.0              # % below peak equity before new risk is blocked
    max_daily_loss_pct: float = 2.0             # % of equity allowed to lose in one day
    max_annualized_volatility_pct: float = 60.0  # skip this check if volatility data isn't supplied


@dataclass
class RiskCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class RiskAssessment:
    checks: List[RiskCheck]
    passed: bool
    position_size_pct: float
    exposure_pct: float
    drawdown_pct: float
    daily_loss_used_pct: float

    @property
    def status(self) -> str:
        return "PROTECTED" if self.passed else "BLOCKED"


def assess_trade(
    *,
    equity: float,
    peak_equity: float,
    proposed_risk_dollars: float,
    proposed_notional: float,
    current_open_notional: float = 0.0,
    realized_pnl_today: float = 0.0,
    recent_returns: Optional[List[float]] = None,
    limits: Optional[RiskLimits] = None,
) -> RiskAssessment:
    """`proposed_risk_dollars` is what you lose if the stop is hit (not the
    full position value); `proposed_notional` is the full position value,
    used for the exposure check. `recent_returns` (daily) is optional — the
    volatility check is skipped, not failed, when it isn't supplied."""
    if limits is None:
        limits = RiskLimits()
    checks: List[RiskCheck] = []

    position_size_pct = (proposed_risk_dollars / equity * 100) if equity else 0.0
    checks.append(
        RiskCheck(
            "Position size",
            position_size_pct <= limits.max_risk_per_trade_pct,
            f"Risking {position_size_pct:.2f}% of equity (limit {limits.max_risk_per_trade_pct:.2f}%)",
        )
    )

    total_notional = current_open_notional + proposed_notional
    exposure_pct = (total_notional / equity * 100) if equity else 0.0
    checks.append(
        RiskCheck(
            "Exposure limit",
            exposure_pct <= limits.max_portfolio_exposure_pct,
            f"Total exposure would be {exposure_pct:.1f}% of equity (limit {limits.max_portfolio_exposure_pct:.1f}%)",
        )
    )

    drawdown_pct = ((peak_equity - equity) / peak_equity * 100) if peak_equity else 0.0
    checks.append(
        RiskCheck(
            "Drawdown",
            drawdown_pct <= limits.max_drawdown_pct,
            f"Currently {drawdown_pct:.1f}% below peak equity (limit {limits.max_drawdown_pct:.1f}%)",
        )
    )

    if recent_returns and len(recent_returns) >= 5:
        mean_r = sum(recent_returns) / len(recent_returns)
        variance = sum((r - mean_r) ** 2 for r in recent_returns) / (len(recent_returns) - 1)
        annualized_vol_pct = math.sqrt(variance) * math.sqrt(252) * 100
        checks.append(
            RiskCheck(
                "Volatility",
                annualized_vol_pct <= limits.max_annualized_volatility_pct,
                f"Annualized volatility {annualized_vol_pct:.1f}% (limit {limits.max_annualized_volatility_pct:.1f}%)",
            )
        )
    else:
        checks.append(RiskCheck("Volatility", True, "Skipped — not enough recent returns supplied"))

    daily_loss_used_pct = (max(0.0, -realized_pnl_today) / equity * 100) if equity else 0.0
    checks.append(
        RiskCheck(
            "Max daily loss",
            daily_loss_used_pct <= limits.max_daily_loss_pct,
            f"Realized today: {realized_pnl_today:+.2f} ({daily_loss_used_pct:.2f}% of equity, limit {limits.max_daily_loss_pct:.2f}%)",
        )
    )

    return RiskAssessment(
        checks=checks,
        passed=all(c.passed for c in checks),
        position_size_pct=position_size_pct,
        exposure_pct=exposure_pct,
        drawdown_pct=drawdown_pct,
        daily_loss_used_pct=daily_loss_used_pct,
    )
