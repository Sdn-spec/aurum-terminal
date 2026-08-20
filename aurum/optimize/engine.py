"""Portfolio optimization: given a matrix of historical returns for the
watchlist, decide how to weight them.

Uses skfolio when it's importable (a real, actively maintained portfolio
optimization library built on scikit-learn — mean-variance, HRP, and more).
If skfolio or its dependency chain isn't available for some reason, a small
pure-numpy mean-variance optimizer takes over so this module never hard-fails
just because one optional package is missing.
"""

from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

try:
    from skfolio import RiskMeasure
    from skfolio.optimization import HierarchicalRiskParity, MeanRisk, ObjectiveFunction

    _SKFOLIO_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when skfolio isn't installed
    _SKFOLIO_AVAILABLE = False


@dataclass
class OptimizationResult:
    weights: Dict[str, float]
    expected_return_annual: float
    volatility_annual: float
    sharpe_ratio: float
    method: str


@dataclass
class CorrelationResult:
    symbols: List[str]
    matrix: List[List[float]]  # matrix[i][j] = correlation between symbols[i] and symbols[j]


def _annualize(daily_mean: float, daily_std: float, periods_per_year: int = 252):
    return daily_mean * periods_per_year, daily_std * np.sqrt(periods_per_year)


def _fallback_mean_variance(returns: pd.DataFrame, risk_aversion: float) -> np.ndarray:
    """Closed-form-ish mean-variance via projected gradient descent: maximize
    (expected return - risk_aversion * variance), long-only, weights sum to 1.
    No cvxpy/solver dependency — this is the safety net, not the primary path.
    """
    n = returns.shape[1]
    mu = returns.mean().to_numpy()
    cov = returns.cov().to_numpy()

    weights = np.full(n, 1.0 / n)
    step = 0.01
    for _ in range(2000):
        grad = mu - 2 * risk_aversion * cov @ weights
        weights = weights + step * grad
        weights = np.clip(weights, 0, None)  # long-only
        total = weights.sum()
        weights = weights / total if total > 0 else np.full(n, 1.0 / n)
    return weights


def optimize(returns: pd.DataFrame, method: str = "mean_risk", risk_aversion: float = 2.0) -> OptimizationResult:
    """`returns` is a DataFrame of daily simple returns, one column per
    instrument (as produced by aurum.optimize.returns_from_bars). `method` is
    "mean_risk" (mean-variance) or "hrp" (Hierarchical Risk Parity, skfolio
    only — falls back to mean-variance if skfolio isn't available)."""
    columns = list(returns.columns)

    if _SKFOLIO_AVAILABLE:
        if method == "hrp":
            model = HierarchicalRiskParity(risk_measure=RiskMeasure.VARIANCE)
        else:
            model = MeanRisk(
                objective_function=ObjectiveFunction.MAXIMIZE_UTILITY,
                risk_aversion=risk_aversion,
                risk_measure=RiskMeasure.VARIANCE,
                min_weights=0.0,  # long-only
            )
        model.fit(returns)
        weights_arr = np.asarray(model.weights_).flatten()
        used_method = f"skfolio:{method}"
    else:
        weights_arr = _fallback_mean_variance(returns, risk_aversion)
        used_method = "fallback:mean_variance"

    weights = {col: float(w) for col, w in zip(columns, weights_arr)}

    portfolio_daily_returns = returns.to_numpy() @ weights_arr
    ann_return, ann_vol = _annualize(portfolio_daily_returns.mean(), portfolio_daily_returns.std())
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0

    return OptimizationResult(
        weights=weights,
        expected_return_annual=float(ann_return),
        volatility_annual=float(ann_vol),
        sharpe_ratio=float(sharpe),
        method=used_method,
    )


def correlation_matrix(returns: pd.DataFrame) -> CorrelationResult:
    """Pairwise Pearson correlation of daily returns across the whole
    watchlist — flags when positions that look diversified by name (Gold,
    Silver, DXY) are actually moving together, something the Optimizer's
    weights alone don't make visible at a glance."""
    corr = returns.corr()
    return CorrelationResult(symbols=list(corr.columns), matrix=corr.to_numpy().tolist())


def returns_from_bars(bars_by_symbol: Dict[str, List]) -> pd.DataFrame:
    """Build an aligned daily-returns DataFrame from {symbol: [HistoryBar,...]}.
    Bars are inner-joined on timestamp so every column has the same dates."""
    closes = {}
    for symbol, bars in bars_by_symbol.items():
        closes[symbol] = pd.Series({bar.timestamp: bar.close for bar in bars})
    prices = pd.DataFrame(closes).dropna().sort_index()
    return prices.pct_change().dropna()
