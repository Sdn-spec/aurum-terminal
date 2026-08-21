"""Headless smoke tests for the Textual terminal app.

Network calls are monkeypatched to synthetic data so these run fast,
deterministically, and independent of Yahoo's current rate-limit state —
this is testing that the UI wiring doesn't crash, not that Yahoo is up.
"""

import os
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import cache, provider, twelvedata, universe, yahoo
from aurum.terminal.app import Aurum


def _fake_history(symbol, range_="10y", interval="1d"):
    import random

    rng = random.Random(zlib.crc32(symbol.encode()))
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
    def setUp(self):
        # This file's whole premise is "no real network", but nothing was
        # enforcing it: the panels reach cache.get_* -> provider.get_*, and
        # provider falls back to Twelve Data using whatever key is sitting in
        # data/config.json. Any call these tests didn't happen to patch was
        # therefore a real request against the machine's real API key -- which
        # is invisible in a worktree (config.json is gitignored, so no key, so
        # no fallback) and live in the actual checkout. That difference is
        # exactly why this went unnoticed, and why these tests were flaky:
        # real network latency makes the UI timing vary run to run.
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config_patch = patch.object(provider, "CONFIG_PATH", Path(self._tmpdir.name) / "config.json")
        self._config_patch.start()
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir.name) / "cache")
        self._cache_patch.start()
        self._env_patch = patch.dict("os.environ", {}, clear=False)
        self._env_patch.start()
        os.environ.pop("TWELVEDATA_API_KEY", None)
        yahoo.reset_rate_limit()
        twelvedata.reset_quota_guard()
        twelvedata.reset_plan_memo()

    def tearDown(self):
        self._config_patch.stop()
        self._cache_patch.stop()
        self._env_patch.stop()
        self._tmpdir.cleanup()
        yahoo.reset_rate_limit()
        twelvedata.reset_quota_guard()
        twelvedata.reset_plan_memo()

    async def test_app_boots_and_populates_watchlist(self):
        # history is patched here too: the boot path loads sparklines, and
        # leaving that unpatched was the actual live network call.
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history), \
             patch("aurum.datafeed.yahoo.get_history", side_effect=_fake_history), \
             patch("aurum.datafeed.yahoo.get_quote", side_effect=_fake_quote):
            app = Aurum()
            async with app.run_test() as pilot:
                await pilot.pause()
                table = app.query_one("#watchlist-table")
                self.assertEqual(table.row_count, len(universe.DEFAULT_WATCHLIST))

    async def test_set_status_survives_the_status_bar_being_gone(self):
        """Regression: the quote refresh is a multi-second worker that reports
        progress as it goes, so quitting mid-refresh leaves it writing to a
        status bar that no longer exists. query_one raised NoMatches there and
        killed the worker with an exception -- which is what made this file
        intermittently fail (~1 run in 12) with
        NoMatches("No nodes match '#status-bar'")."""
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history), \
             patch("aurum.datafeed.yahoo.get_quote", side_effect=_fake_quote):
            app = Aurum()
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#status-bar").remove()
                await pilot.pause()
                app.set_status("must not raise")  # no status bar to write to

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
                # The backtest runs in a background worker (asyncio.to_thread); under
                # heavy concurrent load a fixed pause can end before it finishes, so
                # poll instead of guessing a single sleep duration.
                body_text = ""
                for _ in range(20):
                    await pilot.pause(0.2)
                    body_text = str(app.query_one("#side-body").render())
                    if "Starting equity" in body_text:
                        break
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
