"""The single-input entry point: give it a symbol, get back one detailed
report — a technical read, a bull/bear case built from the same signals the
scanner already confirms, a risk check, and trade plans for both a
day-trade horizon and a long-term hold — ending in one verdict (INVEST /
WATCH / AVOID). Nothing here is a new data source; it's a synthesis layer
over aurum.signals.scanner and aurum.risk.engine, the same two modules the
existing decision memo already uses for the day-trade-only case.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional

from ..datafeed.yahoo import HistoryBar
from ..decision.memo import MIN_RISK_REWARD
from ..risk.engine import RiskAssessment, RiskLimits, assess_trade
from ..signals.scanner import SWING_LOOKBACK, ScanResult, scan as run_scan

LONG_TERM_EMA_PERIOD = 200
LONG_TERM_LOOKBACK_BARS = 252  # ~1 trading year of daily bars, for a 52-week style range
DAY_TRADE_TP1_MULTIPLE = 1.5
DAY_TRADE_TP2_MULTIPLE = 3.0
LONG_TERM_STOP_BUFFER_PCT = 5.0
LONG_TERM_TP1_MULTIPLE = 1.5
LONG_TERM_TP2_MULTIPLE = 3.0
MIN_BARS_FOR_ANALYSIS = LONG_TERM_EMA_PERIOD + 30
# TP1 is deliberately set at exactly MIN_RISK_REWARD's multiple, so a plan that
# clears it should always compare as passing — but reward_per_unit is recovered
# by subtracting two large floats (e.g. tp1 - entry on a $4500 instrument), and
# that subtraction loses enough precision to sometimes land a hair under 1.5.
# A tiny epsilon keeps the boundary case from reading as "1.50:1, below the
# 1.5:1 minimum" — a real contradiction seen live against real Gold data.
_RR_EPSILON = 1e-6


def _meets_min_rr(ratio: float) -> bool:
    return ratio >= MIN_RISK_REWARD - _RR_EPSILON


@dataclass
class HorizonPlan:
    horizon: str  # "day_trade" | "long_term"
    direction: str  # "long" | "short"
    entry: float
    stop: float
    take_profit_1: float
    take_profit_2: float
    risk_per_unit: float
    risk_reward_ratio: float  # reward to TP1, per unit of risk to the stop
    holding_period: str
    notes: List[str]


@dataclass
class ResearchSummary:
    trend_regime: str  # "Uptrend" | "Downtrend" | "Sideways" (based on the 200-day EMA)
    short_term_ema: float
    long_term_ema: float
    momentum_pct: float  # rate of change over the last 21 bars
    volatility_annualized_pct: float
    year_high: float
    year_low: float
    distance_from_year_high_pct: float
    distance_from_year_low_pct: float


@dataclass
class DebateSummary:
    bull_points: List[str]
    bear_points: List[str]


@dataclass
class AnalysisReport:
    symbol: str
    last_close: float
    research: ResearchSummary
    debate: DebateSummary
    scan: ScanResult
    risk: RiskAssessment
    day_trade: HorizonPlan
    long_term: HorizonPlan
    verdict: str  # "INVEST" | "WATCH" | "AVOID"
    confidence: str  # "High" | "Medium" | "Low"
    score: int
    summary: str
    # Optional external context, pre-fetched by the caller (see aurum.web.server)
    # and passed straight through — analyze_symbol() itself stays pure/fast and
    # doesn't make network calls. Informational only: macro/news/earnings never
    # factor into the score above, since how a given instrument actually reacts
    # to a given macro move is instrument-specific and not something to encode
    # as a fixed rule here.
    macro: List[dict] = field(default_factory=list)
    news: List[dict] = field(default_factory=list)
    earnings: Optional[dict] = None


def _ema_series(values: List[float], period: int) -> List[float]:
    k = 2 / (period + 1)
    series = [values[0]]
    for v in values[1:]:
        series.append((v - series[-1]) * k + series[-1])
    return series


def _annualized_volatility_pct(closes: List[float]) -> float:
    returns = [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]
    if len(returns) < 5:
        return 0.0
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252) * 100


def _make_horizon_plan(
    horizon: str,
    direction: str,
    entry: float,
    stop: float,
    tp1_multiple: float,
    tp2_multiple: float,
    holding_period: str,
    notes: List[str],
) -> HorizonPlan:
    risk_per_unit = abs(entry - stop)
    if direction == "long":
        tp1 = entry + risk_per_unit * tp1_multiple
        tp2 = entry + risk_per_unit * tp2_multiple
    else:
        tp1 = entry - risk_per_unit * tp1_multiple
        tp2 = entry - risk_per_unit * tp2_multiple
    rr = (abs(tp1 - entry) / risk_per_unit) if risk_per_unit else 0.0
    return HorizonPlan(
        horizon=horizon,
        direction=direction,
        entry=entry,
        stop=stop,
        take_profit_1=tp1,
        take_profit_2=tp2,
        risk_per_unit=risk_per_unit,
        risk_reward_ratio=rr,
        holding_period=holding_period,
        notes=notes,
    )


def build_research_summary(bars: List[HistoryBar]) -> ResearchSummary:
    closes = [b.close for b in bars]
    short_ema = _ema_series(closes, 50)
    long_ema = _ema_series(closes, LONG_TERM_EMA_PERIOD)
    last_close = closes[-1]

    long_ema_now, long_ema_prev = long_ema[-1], long_ema[-2]
    if last_close > long_ema_now and long_ema_now > long_ema_prev:
        trend_regime = "Uptrend"
    elif last_close < long_ema_now and long_ema_now < long_ema_prev:
        trend_regime = "Downtrend"
    else:
        trend_regime = "Sideways"

    lookback = min(LONG_TERM_LOOKBACK_BARS, len(bars) - 1)
    window = bars[-lookback:]
    year_high = max(b.high for b in window)
    year_low = min(b.low for b in window)

    momentum_pct = (closes[-1] - closes[-21]) / closes[-21] * 100 if len(closes) > 21 else 0.0
    vol_pct = _annualized_volatility_pct(closes[-60:])

    return ResearchSummary(
        trend_regime=trend_regime,
        short_term_ema=short_ema[-1],
        long_term_ema=long_ema_now,
        momentum_pct=momentum_pct,
        volatility_annualized_pct=vol_pct,
        year_high=year_high,
        year_low=year_low,
        distance_from_year_high_pct=(last_close - year_high) / year_high * 100,
        distance_from_year_low_pct=(last_close - year_low) / year_low * 100,
    )


def build_debate(scan: ScanResult, research: ResearchSummary, day_trade: HorizonPlan) -> DebateSummary:
    bull: List[str] = []
    bear: List[str] = []

    trend_conf = next(c for c in scan.confirmations if c.name == "Trend")
    momentum_conf = next(c for c in scan.confirmations if c.name == "Momentum")
    volume_conf = next(c for c in scan.confirmations if c.name == "Volume")
    key_level_conf = next(c for c in scan.confirmations if c.name == "Key level")

    if trend_conf.confirmed and scan.last_close >= scan.ema:
        bull.append(f"Short-term trend is up — {trend_conf.detail}.")
    elif trend_conf.confirmed:
        bear.append(f"Short-term trend is down — {trend_conf.detail}.")
    else:
        bear.append("No confirmed short-term trend yet — the 50-EMA is flat, so this isn't a high-conviction setup.")

    if momentum_conf.confirmed and research.momentum_pct > 0:
        bull.append(f"Momentum is positive: {momentum_conf.detail}.")
    elif momentum_conf.confirmed:
        bear.append(f"Momentum is negative: {momentum_conf.detail}.")
    else:
        bear.append(f"Momentum is flat: {momentum_conf.detail} — not enough thrust to confirm the move.")

    if volume_conf.confirmed:
        bull.append(f"Volume confirms participation: {volume_conf.detail}.")
    else:
        bear.append(f"Volume is unconfirmed: {volume_conf.detail} — the move isn't backed by real interest yet.")

    if key_level_conf.confirmed:
        bull.append(f"Price is at a key level: {key_level_conf.detail}.")

    if research.trend_regime == "Uptrend":
        bull.append(
            f"The 200-day trend is up (price above a rising {research.long_term_ema:.2f} EMA) — "
            "a favorable backdrop for holding longer."
        )
    elif research.trend_regime == "Downtrend":
        bear.append(
            f"The 200-day trend is down (price below a falling {research.long_term_ema:.2f} EMA) — "
            "the long-term backdrop is unfavorable."
        )
    else:
        bear.append("The 200-day trend is sideways — no durable long-term edge either direction right now.")

    if research.distance_from_year_high_pct >= -3:
        bull.append(f"Trading within 3% of its 1-year high ({research.year_high:.2f}) — near-term structure favors continuation.")
    if 0 <= research.distance_from_year_low_pct <= 3:
        bear.append(f"Trading within 3% of its 1-year low ({research.year_low:.2f}) — still inside a weak zone.")

    if research.volatility_annualized_pct >= 40:
        bear.append(
            f"Annualized volatility is elevated at {research.volatility_annualized_pct:.0f}% — "
            "expect large swings, size positions accordingly."
        )

    if _meets_min_rr(day_trade.risk_reward_ratio):
        bull.append(f"The day-trade plan offers a {day_trade.risk_reward_ratio:.2f}:1 reward-to-risk to the first target.")
    else:
        bear.append(
            f"The day-trade plan's reward-to-risk is only {day_trade.risk_reward_ratio:.2f}:1 — "
            f"below the {MIN_RISK_REWARD}:1 minimum."
        )

    return DebateSummary(bull_points=bull, bear_points=bear)


def analyze_symbol(
    symbol: str,
    bars: List[HistoryBar],
    equity: float,
    peak_equity: float,
    realized_pnl_today: float = 0.0,
    limits: Optional[RiskLimits] = None,
    macro: Optional[List[dict]] = None,
    news: Optional[List[dict]] = None,
    earnings: Optional[dict] = None,
) -> AnalysisReport:
    """The full one-input pipeline: scan -> research -> debate -> day-trade
    and long-term plans -> risk gate -> scored verdict. `bars` must be daily
    history with enough bars for a 200-day EMA (see MIN_BARS_FOR_ANALYSIS).
    `macro`/`news`/`earnings` are optional, already-fetched external context
    (see aurum.web.server) — this function makes no network calls itself."""
    if len(bars) < MIN_BARS_FOR_ANALYSIS:
        raise ValueError(f"need at least {MIN_BARS_FOR_ANALYSIS} daily bars for a full analysis, got {len(bars)}")

    scan_result = run_scan(bars, symbol)
    research = build_research_summary(bars)

    swing_window = bars[-SWING_LOOKBACK - 1 : -1]
    swing_low = min(b.low for b in swing_window)
    swing_high = max(b.high for b in swing_window)
    day_direction = "long" if scan_result.last_close >= scan_result.ema else "short"
    day_stop = swing_low * 0.998 if day_direction == "long" else swing_high * 1.002
    day_trade = _make_horizon_plan(
        "day_trade",
        day_direction,
        scan_result.last_close,
        day_stop,
        DAY_TRADE_TP1_MULTIPLE,
        DAY_TRADE_TP2_MULTIPLE,
        "Intraday to a few days — reassess against the 50-EMA and the 20-bar swing range.",
        [f"Pattern read: {scan_result.pattern} ({scan_result.score_pct:.0f}% of signals confirmed)."],
    )

    long_direction = "long" if research.trend_regime != "Downtrend" else "short"
    if long_direction == "long":
        long_stop = research.long_term_ema * (1 - LONG_TERM_STOP_BUFFER_PCT / 100)
    else:
        long_stop = research.long_term_ema * (1 + LONG_TERM_STOP_BUFFER_PCT / 100)
    long_term = _make_horizon_plan(
        "long_term",
        long_direction,
        scan_result.last_close,
        long_stop,
        LONG_TERM_TP1_MULTIPLE,
        LONG_TERM_TP2_MULTIPLE,
        "Weeks to months — reassess monthly against the 200-day EMA.",
        [f"Primary trend: {research.trend_regime} (price vs 200-day EMA {research.long_term_ema:.2f})."],
    )

    debate = build_debate(scan_result, research, day_trade)

    risk_budget = equity * 0.01
    units = risk_budget / day_trade.risk_per_unit if day_trade.risk_per_unit > 0 else 0.0
    recent_closes = [b.close for b in bars[-60:]]
    recent_returns = [(recent_closes[i] / recent_closes[i - 1]) - 1.0 for i in range(1, len(recent_closes))]
    risk_result = assess_trade(
        equity=equity,
        peak_equity=peak_equity,
        proposed_risk_dollars=risk_budget,
        proposed_notional=day_trade.entry * units,
        realized_pnl_today=realized_pnl_today,
        recent_returns=recent_returns,
        limits=limits,
    )

    score = len(debate.bull_points) - len(debate.bear_points)
    if _meets_min_rr(day_trade.risk_reward_ratio):
        score += 1
    if research.trend_regime == "Uptrend":
        score += 1
    elif research.trend_regime == "Downtrend":
        score -= 1
    if not risk_result.passed:
        score -= 2

    if not risk_result.passed:
        verdict = "AVOID"
    elif score >= 3:
        verdict = "INVEST"
    elif score <= -2:
        verdict = "AVOID"
    else:
        verdict = "WATCH"

    confidence = "High" if abs(score) >= 4 else "Medium" if abs(score) >= 2 else "Low"

    summary = (
        f"{symbol}: {verdict} ({confidence} confidence). "
        f"{scan_result.pattern} setup on the day-trade read ({scan_result.score_pct:.0f}% confirmed), "
        f"{research.trend_regime.lower()} on the 200-day trend. "
        f"Risk gate {'passed' if risk_result.passed else 'BLOCKED'}. "
        f"{len(debate.bull_points)} bullish point(s) vs {len(debate.bear_points)} bearish point(s)."
    )

    return AnalysisReport(
        symbol=symbol,
        last_close=scan_result.last_close,
        research=research,
        debate=debate,
        scan=scan_result,
        risk=risk_result,
        day_trade=day_trade,
        long_term=long_term,
        verdict=verdict,
        confidence=confidence,
        score=score,
        summary=summary,
        macro=macro or [],
        news=news or [],
        earnings=earnings,
    )
