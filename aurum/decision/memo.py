"""The one thing every other module feeds into: a plain-language memo that
says take it, watch it, or skip it — and why. This never places a trade;
it exists so a human (you) makes the final call with everything relevant
in front of you at once, the same way the reference architecture's "human
review required" step works.
"""

from dataclasses import dataclass
from typing import List

from ..risk.engine import RiskAssessment
from ..signals.scanner import ScanResult

MIN_RISK_REWARD = 1.5


@dataclass
class TradePlan:
    direction: str  # "long" | "short"
    entry: float
    stop: float
    target: float

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.target - self.entry)

    @property
    def risk_reward_ratio(self) -> float:
        return self.reward_per_unit / self.risk_per_unit if self.risk_per_unit else 0.0


@dataclass
class DecisionMemo:
    symbol: str
    scan: ScanResult
    risk: RiskAssessment
    plan: TradePlan
    verdict: str  # "APPROVED" | "WATCHLIST" | "REJECTED"
    reasons: List[str]


def build_trade_plan(scan: ScanResult, swing_low: float, swing_high: float, stop_buffer_pct: float = 0.2,
                      reward_multiple: float = 2.0) -> TradePlan:
    """A long plan if the last known trend read is up, short if down — stop
    just past the relevant swing extreme, target at `reward_multiple`x the
    resulting risk. Same shape as the Trend Pullback strategy's own rules,
    generalized so any scan result (not just a pullback) can produce a plan."""
    direction = "long" if scan.last_close >= scan.ema else "short"
    if direction == "long":
        stop = swing_low * (1 - stop_buffer_pct / 100)
        risk = scan.last_close - stop
        target = scan.last_close + risk * reward_multiple
    else:
        stop = swing_high * (1 + stop_buffer_pct / 100)
        risk = stop - scan.last_close
        target = scan.last_close - risk * reward_multiple
    return TradePlan(direction=direction, entry=scan.last_close, stop=stop, target=target)


def build_memo(symbol: str, scan: ScanResult, risk: RiskAssessment, plan: TradePlan) -> DecisionMemo:
    reasons: List[str] = []

    if not risk.passed:
        failed = ", ".join(c.name for c in risk.checks if not c.passed)
        reasons.append(f"Risk gate failed: {failed}")

    if not scan.setup_detected:
        reasons.append(f"Setup score is only {scan.score_pct:.0f}% — fewer than 3 of 4 signals confirmed")

    if plan.risk_reward_ratio < MIN_RISK_REWARD:
        reasons.append(f"Risk/reward is {plan.risk_reward_ratio:.2f} — below the {MIN_RISK_REWARD} minimum")

    if not risk.passed:
        # A failed risk check is a hard block regardless of how good the setup looks —
        # matches the reference risk module's "fail -> block", no exceptions.
        verdict = "REJECTED"
    elif scan.setup_detected and plan.risk_reward_ratio >= MIN_RISK_REWARD:
        verdict = "APPROVED"
    else:
        verdict = "WATCHLIST"

    return DecisionMemo(symbol=symbol, scan=scan, risk=risk, plan=plan, verdict=verdict, reasons=reasons)
