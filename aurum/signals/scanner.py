"""Scans a bar series for the same four confirmations a discretionary trader
checks by eye — trend, momentum, volume, and proximity to a key level — then
classifies what kind of setup they add up to (Breakout, Pullback, Momentum,
Trend Continuation, Reversal, or nothing). This doesn't decide whether to
trade; `aurum.decision.memo` combines this with the risk gate for that.
"""

import math
from dataclasses import dataclass
from typing import List

from ..datafeed.yahoo import HistoryBar

MOMENTUM_THRESHOLD_PCT = 1.0    # |rate of change| over MOMENTUM_LOOKBACK bars to count as "confirmed"
MOMENTUM_LOOKBACK = 10
VOLUME_MULTIPLE = 1.2           # last bar's volume vs the trailing average to count as "confirmed"
EMA_PROXIMITY_PCT = 0.5         # within this % of the EMA counts as "pulled back to it"
SWING_LOOKBACK = 20             # bars used to define the recent swing high/low
SWING_PROXIMITY_PCT = 0.15      # within this % of the swing extreme counts as "at a key level"


@dataclass
class Confirmation:
    name: str
    confirmed: bool
    detail: str


@dataclass
class ScanResult:
    symbol: str
    pattern: str  # "Breakout" | "Pullback" | "Momentum" | "Trend Continuation" | "Reversal" | "None"
    confirmations: List[Confirmation]
    score_pct: float  # share of the 4 confirmations that passed
    setup_detected: bool  # score_pct >= 75 (at least 3 of 4)
    last_close: float
    ema: float


def _ema_series(closes: List[float], period: int) -> List[float]:
    k = 2 / (period + 1)
    series = [closes[0]]
    for c in closes[1:]:
        series.append((c - series[-1]) * k + series[-1])
    return series


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def scan(bars: List[HistoryBar], symbol: str, ema_period: int = 50) -> ScanResult:
    min_len = max(ema_period, SWING_LOOKBACK, MOMENTUM_LOOKBACK) + 5
    if len(bars) < min_len:
        raise ValueError(f"need at least {min_len} bars to scan, got {len(bars)}")

    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    ema = _ema_series(closes, ema_period)
    ema_now, ema_prev = ema[-1], ema[-2]
    last_close = closes[-1]

    trend_up = ema_now > ema_prev and last_close > ema_now
    trend_down = ema_now < ema_prev and last_close < ema_now
    trend_confirmed = trend_up or trend_down
    trend_detail = (
        f"50-EMA {'rising' if ema_now > ema_prev else 'falling' if ema_now < ema_prev else 'flat'}, "
        f"price {'above' if last_close > ema_now else 'below'} it"
    )

    roc_pct = (closes[-1] - closes[-1 - MOMENTUM_LOOKBACK]) / closes[-1 - MOMENTUM_LOOKBACK] * 100
    momentum_confirmed = abs(roc_pct) >= MOMENTUM_THRESHOLD_PCT
    momentum_detail = f"{roc_pct:+.2f}% over the last {MOMENTUM_LOOKBACK} bars"

    trailing_avg_volume = _mean(volumes[-21:-1]) if len(volumes) > 21 else _mean(volumes[:-1])
    volume_confirmed = trailing_avg_volume > 0 and volumes[-1] > trailing_avg_volume * VOLUME_MULTIPLE
    volume_detail = f"last bar {volumes[-1]:,.0f} vs {VOLUME_MULTIPLE:.1f}x avg {trailing_avg_volume:,.0f}"

    swing_window = bars[-SWING_LOOKBACK - 1 : -1]  # exclude the current bar itself
    swing_high = max(b.high for b in swing_window)
    swing_low = min(b.low for b in swing_window)
    near_high = abs(last_close - swing_high) / last_close * 100 <= SWING_PROXIMITY_PCT
    near_low = abs(last_close - swing_low) / last_close * 100 <= SWING_PROXIMITY_PCT
    broke_above = last_close > swing_high
    broke_below = last_close < swing_low
    at_key_level = near_high or near_low or broke_above or broke_below
    key_level_detail = f"{SWING_LOOKBACK}-bar range {swing_low:.2f}-{swing_high:.2f}, last close {last_close:.2f}"

    confirmations = [
        Confirmation("Trend", trend_confirmed, trend_detail),
        Confirmation("Momentum", momentum_confirmed, momentum_detail),
        Confirmation("Volume", volume_confirmed, volume_detail),
        Confirmation("Key level", at_key_level, key_level_detail),
    ]
    score_pct = sum(c.confirmed for c in confirmations) / len(confirmations) * 100

    ema_distance_pct = abs(last_close - ema_now) / ema_now * 100 if ema_now else math.inf
    near_ema = ema_distance_pct <= EMA_PROXIMITY_PCT

    pattern = "None"
    if (broke_above or broke_below) and volume_confirmed:
        pattern = "Breakout"
    elif trend_confirmed and at_key_level and (
        (trend_up and roc_pct < 0) or (trend_down and roc_pct > 0)
    ):
        pattern = "Reversal"
    elif trend_confirmed and near_ema:
        pattern = "Pullback"
    elif trend_confirmed and momentum_confirmed and (
        (trend_up and roc_pct > 0) or (trend_down and roc_pct < 0)
    ):
        pattern = "Momentum"
    elif trend_confirmed:
        pattern = "Trend Continuation"

    return ScanResult(
        symbol=symbol,
        pattern=pattern,
        confirmations=confirmations,
        score_pct=score_pct,
        setup_detected=score_pct >= 75.0,
        last_close=last_close,
        ema=ema_now,
    )
