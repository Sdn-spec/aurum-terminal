"""The one thing every other module feeds into: a plain-language memo that
says take it, watch it, or skip it — and why. This never places a trade;
it exists so a human (you) makes the final call with everything relevant
in front of you at once, the same way the reference architecture's "human
review required" step works.
"""

from dataclasses import dataclass
from typing import List, Optional

from ..datafeed.yahoo import HistoryBar
from ..risk.engine import RiskAssessment, RiskLimits, assess_trade
from ..signals.scanner import SWING_LOOKBACK, ScanResult, scan as run_scan

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


def decide_for_symbol(
    symbol: str,
    bars: List[HistoryBar],
    equity: float,
    peak_equity: float,
    realized_pnl_today: float = 0.0,
    limits: Optional[RiskLimits] = None,
) -> DecisionMemo:
    """The full pipeline for one symbol: scan -> trade plan -> size to 1% risk
    -> risk gate -> memo. This is the single source of truth both the
    single-symbol `/api/decision` endpoint and the fund-wide scanner use —
    they were two copies of this exact logic before, which is exactly the
    kind of thing that quietly drifts apart if left duplicated."""
    scan_result = run_scan(bars, symbol)  # raises ValueError if there's not enough history yet

    swing_window = bars[-SWING_LOOKBACK - 1 : -1]
    swing_low = min(b.low for b in swing_window)
    swing_high = max(b.high for b in swing_window)
    plan = build_trade_plan(scan_result, swing_low, swing_high)

    risk_budget = equity * 0.01
    units = risk_budget / plan.risk_per_unit if plan.risk_per_unit > 0 else 0.0
    closes = [b.close for b in bars[-60:]]
    recent_returns = [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]
    risk_result = assess_trade(
        equity=equity,
        peak_equity=peak_equity,
        proposed_risk_dollars=risk_budget,
        proposed_notional=plan.entry * units,
        realized_pnl_today=realized_pnl_today,
        recent_returns=recent_returns,
        limits=limits,
    )

    return build_memo(symbol, scan_result, risk_result, plan)
