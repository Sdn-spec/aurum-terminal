"""A statistical baseline forecaster: no training, no weights, no GPU — just
the recent drift and volatility of the series, projected forward as a
random walk with drift. This is deliberately simple.

Its purpose isn't to be a great forecaster (a plain random walk rarely beats
"tomorrow looks like today" by much). Its purpose is to always be available
and honest about its own uncertainty, and to be the thing any fancier model
(Kronos included) has to actually beat before it's worth trusting.
"""

import math
from typing import List

from .base import ForecastResult


def forecast(closes: List[float], horizon: int = 10, confidence_z: float = 1.645) -> ForecastResult:
    """`confidence_z`=1.645 gives a ~90% band under a normal-returns
    assumption; that assumption is the whole caveat, see `note` below."""
    if len(closes) < 20:
        raise ValueError("need at least 20 closes to estimate drift/volatility")

    returns = [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(variance)

    last = closes[-1]
    points, lower, upper = [], [], []
    for h in range(1, horizon + 1):
        point = last * (1 + mean_r) ** h
        band = last * confidence_z * std_r * math.sqrt(h)
        points.append(point)
        lower.append(point - band)
        upper.append(point + band)

    return ForecastResult(
        horizon=horizon,
        point_forecast=points,
        lower_band=lower,
        upper_band=upper,
        method="baseline:random_walk_with_drift",
        note=(
            "Assumes daily returns are i.i.d. normal, which real markets aren't "
            "(fat tails, volatility clustering) — treat the band as a rough "
            "sanity check, not a real confidence interval."
        ),
    )
