"""The terminal itself: a Textual TUI that ties every other module together
behind one screen — watchlist quotes, a price sparkline, portfolio
optimization, forecasting (baseline + Kronos), and a one-key backtest of the
Trend Pullback strategy against real cached history.

Everything that touches the network or does real computation runs in a
Textual worker (off the UI thread) — a slow or rate-limited Yahoo call never
freezes the interface, it just shows a status message until it resolves.
"""

import asyncio
from datetime import datetime, timezone

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Sparkline, Static

from ..datafeed import cache, universe, yahoo
from ..optimize import engine as optimize_engine

REFRESH_HINT = "r refresh quotes  |  o optimize  |  f forecast  |  k kronos forecast  |  b backtest  |  q quit"


class Aurum(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    #body {
        height: 1fr;
    }
    #watchlist-pane {
        width: 38%;
        border: solid $accent;
    }
    #chart-pane {
        width: 1fr;
        border: solid $accent;
    }
    #side-pane {
        width: 34%;
        border: solid $accent;
    }
    .pane-title {
        background: $accent 20%;
        color: $text;
        padding: 0 1;
        text-style: bold;
    }
    #status-bar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    #side-content, #chart-content {
        padding: 1;
    }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh quotes"),
        ("o", "run_optimizer", "Optimize"),
        ("f", "run_forecast", "Forecast"),
        ("k", "run_kronos", "Kronos forecast"),
        ("b", "run_backtest", "Backtest"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.watchlist = universe.DEFAULT_WATCHLIST
        self.selected = self.watchlist[0]
        self.history_cache = {}  # friendly name -> list[HistoryBar], filled in on demand

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="watchlist-pane"):
                yield Static("WATCHLIST", classes="pane-title")
                yield DataTable(id="watchlist-table", cursor_type="row")
            with Vertical(id="chart-pane"):
                yield Static("PRICE — select a row (↑/↓ then Enter)", id="chart-title", classes="pane-title")
                with VerticalScroll(id="chart-content"):
                    yield Sparkline([], id="chart-sparkline")
                    yield Static("", id="chart-summary")
            with Vertical(id="side-pane"):
                yield Static("PANEL", id="side-title", classes="pane-title")
                with VerticalScroll(id="side-content"):
                    yield Static("Press o / f / k / b to run a panel here.", id="side-body")
        yield Static(REFRESH_HINT, id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#watchlist-table", DataTable)
        table.add_column("Symbol", key="symbol")
        table.add_column("Ticker", key="ticker")
        table.add_column("Last", key="last")
        table.add_column("Day High", key="high")
        table.add_column("Day Low", key="low")
        table.add_column("Updated", key="updated")
        for name in self.watchlist:
            table.add_row(name, universe.resolve(name), "—", "—", "—", "—", key=name)
        self.set_status("Ready. Press r to fetch live quotes.")
        self.run_worker(self._refresh_quotes(), exclusive=True, group="quotes")

    def set_status(self, message: str) -> None:
        self.query_one("#status-bar", Static).update(message)

    # ---- data fetching (workers) --------------------------------------

    async def _refresh_quotes(self) -> None:
        self.set_status("Fetching quotes…")
        table = self.query_one("#watchlist-table", DataTable)
        errors = []
        for name in self.watchlist:
            ticker = universe.resolve(name)
            try:
                quote = await asyncio.to_thread(yahoo.get_quote, ticker)
                updated = datetime.fromtimestamp(quote.market_time, tz=timezone.utc).strftime("%H:%M:%S UTC")
                table.update_cell(name, "last", f"{quote.price:,.2f}")
                table.update_cell(name, "high", f"{quote.day_high:,.2f}")
                table.update_cell(name, "low", f"{quote.day_low:,.2f}")
                table.update_cell(name, "updated", updated)
            except yahoo.DataFeedError as e:
                errors.append(f"{name}: {e}")
        if errors:
            self.set_status(f"Done with {len(errors)} error(s) — {errors[0]}")
        else:
            self.set_status(f"Quotes updated {datetime.now().strftime('%H:%M:%S')}")

    async def _get_history(self, name: str):
        if name in self.history_cache:
            return self.history_cache[name]
        ticker = universe.resolve(name)
        bars = await asyncio.to_thread(cache.get_history, ticker, "10y", "1d")
        self.history_cache[name] = bars
        return bars

    # ---- actions --------------------------------------------------------

    def action_refresh(self) -> None:
        self.run_worker(self._refresh_quotes(), exclusive=True, group="quotes")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value:
            self.selected = event.row_key.value
            self.query_one("#chart-title", Static).update(f"PRICE — {self.selected}")
            self.run_worker(self._update_chart(), exclusive=True, group="chart")

    async def _update_chart(self) -> None:
        try:
            bars = await self._get_history(self.selected)
        except yahoo.DataFeedError as e:
            self.query_one("#chart-summary", Static).update(f"[red]Could not load history: {e}[/red]")
            return
        closes = [b.close for b in bars[-180:]]
        self.query_one("#chart-sparkline", Sparkline).data = closes
        if closes:
            change = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0.0
            self.query_one("#chart-summary", Static).update(
                f"{self.selected}: {len(bars)} daily bars cached  ·  last {len(closes)} shown  ·  "
                f"{change:+.1f}% over that window"
            )

    def action_run_optimizer(self) -> None:
        self.run_worker(self._run_optimizer(), exclusive=True, group="side")

    async def _run_optimizer(self) -> None:
        self.query_one("#side-title", Static).update("PANEL — Portfolio Optimizer")
        body = self.query_one("#side-body", Static)
        body.update("Loading history for the watchlist…")
        bars_by_symbol = {}
        for name in self.watchlist:
            try:
                bars_by_symbol[name] = await self._get_history(name)
            except yahoo.DataFeedError as e:
                body.update(f"[red]Could not load {name}: {e}[/red]")
                return
        returns = optimize_engine.returns_from_bars(bars_by_symbol)
        if len(returns) < 30:
            body.update("[yellow]Not enough overlapping history yet to optimize.[/yellow]")
            return
        result = optimize_engine.optimize(returns, method="hrp")
        lines = [f"Method: {result.method}", ""]
        for name, w in sorted(result.weights.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name:<10} {w * 100:5.1f}%")
        lines += [
            "",
            f"Ann. return   {result.expected_return_annual * 100:+.2f}%",
            f"Ann. vol      {result.volatility_annual * 100:.2f}%",
            f"Sharpe        {result.sharpe_ratio:.2f}",
        ]
        body.update("\n".join(lines))

    def action_run_forecast(self) -> None:
        self.run_worker(self._run_forecast(), exclusive=True, group="side")

    async def _run_forecast(self) -> None:
        from ..forecast import baseline

        self.query_one("#side-title", Static).update(f"PANEL — Baseline Forecast ({self.selected})")
        body = self.query_one("#side-body", Static)
        try:
            bars = await self._get_history(self.selected)
        except yahoo.DataFeedError as e:
            body.update(f"[red]{e}[/red]")
            return
        closes = [b.close for b in bars]
        result = baseline.forecast(closes, horizon=10)
        lines = [f"Method: {result.method}", ""]
        for h, (p, lo, hi) in enumerate(zip(result.point_forecast, result.lower_band, result.upper_band), start=1):
            lines.append(f"  +{h:>2}d   {p:>9,.2f}   [{lo:,.2f} .. {hi:,.2f}]")
        lines += ["", result.note]
        body.update("\n".join(lines))

    def action_run_kronos(self) -> None:
        self.run_worker(self._run_kronos(), exclusive=True, group="side")

    async def _run_kronos(self) -> None:
        from ..forecast import kronos_adapter

        self.query_one("#side-title", Static).update(f"PANEL — Kronos Forecast ({self.selected})")
        body = self.query_one("#side-body", Static)
        body.update("Loading Kronos-mini (first run downloads ~30MB of weights)…")
        try:
            bars = await self._get_history(self.selected)
        except yahoo.DataFeedError as e:
            body.update(f"[red]{e}[/red]")
            return

        import pandas as pd

        df = pd.DataFrame(
            {
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [b.volume for b in bars],
            }
        )
        timestamps = pd.Series(pd.to_datetime([b.timestamp for b in bars], unit="s"))

        try:
            result = await asyncio.to_thread(kronos_adapter.forecast, df, timestamps, 10)
        except Exception as e:  # noqa: BLE001
            body.update(f"[red]Kronos unavailable: {e}[/red]")
            return

        lines = [f"Method: {result.method}", ""]
        for h, p in enumerate(result.point_forecast, start=1):
            lines.append(f"  +{h:>2}d   {p:>9,.2f}")
        lines += ["", result.note]
        body.update("\n".join(lines))

    def action_run_backtest(self) -> None:
        self.run_worker(self._run_backtest(), exclusive=True, group="side")

    async def _run_backtest(self) -> None:
        from ..backtest.adapter import run_strategy
        from strategies.trend_pullback import TrendPullbackStrategy

        self.query_one("#side-title", Static).update(f"PANEL — Trend Pullback Backtest ({self.selected})")
        body = self.query_one("#side-body", Static)
        try:
            bars = await self._get_history(self.selected)
        except yahoo.DataFeedError as e:
            body.update(f"[red]{e}[/red]")
            return

        strategy = TrendPullbackStrategy(self.selected, risk_per_trade=30.0)
        portfolio, stats = await asyncio.to_thread(run_strategy, strategy, bars, self.selected, 3000.0)
        lines = [
            f"Data: {len(bars)} real daily bars",
            "",
            f"Starting equity   {stats.starting_equity:>10,.2f}",
            f"Ending equity     {stats.ending_equity:>10,.2f}",
            f"Total return      {stats.total_return_pct:>9.2f}%",
            f"Trades            {stats.total_trades:>10d}",
            f"Win rate          {stats.win_rate_pct:>9.1f}%",
            f"Avg win/loss   {stats.avg_win:>7,.2f} / {stats.avg_loss:,.2f}",
            f"Profit factor     {stats.profit_factor:>10.2f}",
            f"Max drawdown      {stats.max_drawdown_pct:>9.2f}%",
        ]
        body.update("\n".join(lines))


def main() -> None:
    Aurum().run()


if __name__ == "__main__":
    main()
