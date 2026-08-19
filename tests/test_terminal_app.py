"""Headless smoke tests for the Textual terminal app.

Network calls are monkeypatched to synthetic data so these run fast,
deterministically, and independent of Yahoo's current rate-limit state —
this is testing that the UI wiring doesn't crash, not that Yahoo is up.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import universe, yahoo
from aurum.terminal.app import Aurum


def _fake_history(symbol, range_="10y", interval="1d"):
    import random

    rng = random.Random(hash(symbol) % (2**32))
    bars, price, ts = [], 100.0, 1_700_000_000
    for _ in range(400):
        o = price
        c = max(0.01, o + rng.gauss(0, 1.5))
        h, l = max(o, c) + 0.3, min(o, c) - 0.3
        bars.append(yahoo.HistoryBar(ts, o, h, l, c, 1000))
        price, ts = c, ts + 86400
    return bars


def _fake_quote(symbol):
    return yahoo.Quote(symbol, 123.45, 125.0, 121.0, 150.0, 90.0, "USD", "TEST", 1_700_000_000)


class TestTerminalApp(unittest.IsolatedAsyncioTestCase):
    async def test_app_boots_and_populates_watchlist(self):
        with patch("aurum.datafeed.yahoo.get_quote", side_effect=_fake_quote):
            app = Aurum()
            async with app.run_test() as pilot:
                await pilot.pause()
                table = app.query_one("#watchlist-table")
                self.assertEqual(table.row_count, len(universe.DEFAULT_WATCHLIST))

    async def test_optimizer_panel_runs_without_crashing(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history), \
             patch("aurum.datafeed.yahoo.get_quote", side_effect=_fake_quote):
            app = Aurum()
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("o")
                await pilot.pause(0.2)
                body_text = app.query_one("#side-body").render()
                self.assertIn("Method", str(body_text))

    async def test_backtest_panel_runs_without_crashing(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history), \
             patch("aurum.datafeed.yahoo.get_quote", side_effect=_fake_quote):
            app = Aurum()
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("b")
                await pilot.pause(0.3)
                body_text = str(app.query_one("#side-body").render())
                self.assertIn("Starting equity", body_text)

    async def test_forecast_panel_runs_without_crashing(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history), \
             patch("aurum.datafeed.yahoo.get_quote", side_effect=_fake_quote):
            app = Aurum()
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("f")
                await pilot.pause(0.2)
                body_text = str(app.query_one("#side-body").render())
                self.assertIn("Method", body_text)


if __name__ == "__main__":
    unittest.main()
