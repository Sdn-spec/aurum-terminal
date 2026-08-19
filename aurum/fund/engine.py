""""Your entire AI hedge fund" (the reference architecture's own words) —
run the scan -> risk -> decide pipeline across the whole watchlist at once,
then suggest a capital split across whatever actually cleared the bar.

This is not a second implementation of the decision pipeline: it calls
`decision.memo.decide_for_symbol` once per symbol, the exact same function
the single-symbol `/api/decision` endpoint uses. What this module adds is
purely the aggregation — running it over many symbols and turning the
results that passed into a portfolio question.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..datafeed.yahoo import HistoryBar
from ..decision.memo import DecisionMemo, decide_for_symbol
from ..optimize.engine import OptimizationResult, optimize, returns_from_bars
from ..risk.engine import RiskLimits

MIN_BARS_FOR_ALLOCATION = 30


@dataclass
class FundEntry:
    symbol: str
    memo: Optional[DecisionMemo]
    error: Optional[str]  # set instead of memo when there wasn't enough history to scan


@dataclass
class FundReport:
    entries: List[FundEntry]
    allocation: Optional[OptimizationResult]  # None if fewer than 2 symbols had enough data to optimize over
    approved_symbols: List[str]
    watchlist_symbols: List[str]


def scan_watchlist(
    bars_by_symbol: Dict[str, List[HistoryBar]],
    equity: float,
    peak_equity: float,
    realized_pnl_today: float = 0.0,
    limits: Optional[RiskLimits] = None,
) -> FundReport:
    entries: List[FundEntry] = []
    for symbol, bars in bars_by_symbol.items():
        try:
            memo = decide_for_symbol(symbol, bars, equity, peak_equity, realized_pnl_today, limits)
            entries.append(FundEntry(symbol=symbol, memo=memo, error=None))
        except ValueError as e:
            entries.append(FundEntry(symbol=symbol, memo=None, error=str(e)))

    approved = [e.symbol for e in entries if e.memo and e.memo.verdict == "APPROVED"]
    watchlisted = [e.symbol for e in entries if e.memo and e.memo.verdict == "WATCHLIST"]

    # Allocate across whatever's actually approved right now; if fewer than 2
    # names cleared the bar there's no allocation question to answer yet, so
    # fall back to the full scanned universe just to show a reference split.
    alloc_universe = approved if len(approved) >= 2 else [e.symbol for e in entries if e.memo is not None]

    allocation = None
    if len(alloc_universe) >= 2:
        subset = {s: bars_by_symbol[s] for s in alloc_universe}
        returns = returns_from_bars(subset)
        if len(returns) >= MIN_BARS_FOR_ALLOCATION:
            try:
                allocation = optimize(returns, method="hrp")
            except Exception:  # noqa: BLE001 - skfolio can raise on a near-singular/degenerate
                # correlation structure (e.g. near-duplicate return series); an allocation
                # suggestion failing outright shouldn't take the whole fund scan down with it.
                allocation = None

    return FundReport(entries=entries, allocation=allocation, approved_symbols=approved, watchlist_symbols=watchlisted)
