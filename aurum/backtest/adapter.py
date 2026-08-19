"""Bridges aurum's data layer to nautilus-mini's backtest engine, instead of
duplicating that engine here. nautilus-mini already has a tested
Strategy/Engine/Broker/Portfolio design (see ../nautilus-mini) — this module
just converts aurum's HistoryBar rows into naut_mini.Bar objects and runs
them through it.
"""

import sys
from pathlib import Path
from typing import List

_NAUTILUS_MINI_PATH = Path(__file__).resolve().parents[3] / "nautilus-mini"
if str(_NAUTILUS_MINI_PATH) not in sys.path:
    sys.path.insert(0, str(_NAUTILUS_MINI_PATH))

from naut_mini import BacktestEngine, Portfolio, SimulatedBroker  # noqa: E402
from naut_mini import metrics as naut_metrics  # noqa: E402
from naut_mini.events import Bar  # noqa: E402

from ..datafeed.yahoo import HistoryBar  # noqa: E402


def bars_from_history(history_bars: List[HistoryBar], instrument: str) -> List[Bar]:
    from datetime import datetime, timezone

    return [
        Bar(
            timestamp=datetime.fromtimestamp(hb.timestamp, tz=timezone.utc),
            instrument=instrument,
            open=hb.open,
            high=hb.high,
            low=hb.low,
            close=hb.close,
            volume=hb.volume,
        )
        for hb in history_bars
    ]


def run_strategy(strategy, history_bars: List[HistoryBar], instrument: str, starting_cash: float):
    """Runs `strategy` (any naut_mini.Strategy) over real historical bars.
    Returns (portfolio, stats) exactly like nautilus-mini's own examples do."""
    bars = bars_from_history(history_bars, instrument)
    portfolio = Portfolio(starting_cash=starting_cash)
    broker = SimulatedBroker(portfolio)
    engine = BacktestEngine(bars, strategy, portfolio, broker)
    engine.run()
    stats = naut_metrics.compute_stats(portfolio)
    return portfolio, stats
