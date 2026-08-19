"""API-level tests for the FastAPI backend. Network calls are mocked —
these test the web layer's wiring and error handling, not Yahoo's uptime."""

import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from aurum.datafeed import cache, yahoo
from aurum.web import server


def _fake_history(symbol, range_="10y", interval="1d"):
    import random

    rng = random.Random(zlib.crc32(symbol.encode()))
    bars, price, ts = [], 100.0, 1_700_000_000
    for _ in range(400):
        o = price
        c = max(0.01, o + rng.gauss(0, 1.2))
        h, l = max(o, c) + 0.3, min(o, c) - 0.3
        bars.append(yahoo.HistoryBar(ts, o, h, l, c, abs(rng.gauss(1000, 200))))
        price, ts = c, ts + 86400
    return bars


def _fake_quote(symbol):
    return yahoo.Quote(symbol, 123.45, 125.0, 121.0, 150.0, 90.0, "USD", "TEST", 1_700_000_000)


class TestWebServer(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self._tmpdir = tempfile.TemporaryDirectory()
        self._state_patch = patch.object(server, "STATE_PATH", Path(self._tmpdir.name) / "state.json")
        self._state_patch.start()
        # cache.get_quote/get_history read/write real files under CACHE_DIR — isolate
        # that to a tempdir too, or these tests would pollute (and be polluted by)
        # the real data/cache/ directory.
        self._cache_patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir.name) / "cache")
        self._cache_patch.start()

    def tearDown(self):
        self._state_patch.stop()
        self._cache_patch.stop()
        self._tmpdir.cleanup()

    def test_index_serves_html(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Aurum", res.text)

    def test_watchlist(self):
        res = self.client.get("/api/watchlist")
        self.assertEqual(res.status_code, 200)
        names = [row["name"] for row in res.json()]
        self.assertIn("GOLD", names)

    def test_quote_success(self):
        with patch("aurum.datafeed.yahoo.get_quote", side_effect=_fake_quote):
            res = self.client.get("/api/quote/GOLD")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["price"], 123.45)

    def test_quote_failure_returns_502_not_crash(self):
        with patch("aurum.datafeed.yahoo.get_quote", side_effect=yahoo.DataFeedError("rate limited")):
            res = self.client.get("/api/quote/GOLD")
        self.assertEqual(res.status_code, 502)
        self.assertIn("rate limited", res.json()["detail"])

    def test_state_round_trip(self):
        res = self.client.get("/api/state")
        self.assertEqual(res.json()["equity"], 3000.0)
        res = self.client.post("/api/state", json={"equity": 3289.63})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["equity"], 3289.63)
        res = self.client.get("/api/state")
        self.assertEqual(res.json()["equity"], 3289.63)

    def test_scan_endpoint(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/scan/GOLD")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("pattern", body)
        self.assertEqual(len(body["confirmations"]), 4)

    def test_decision_endpoint_returns_verdict(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/decision/GOLD")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn(body["verdict"], ["APPROVED", "WATCHLIST", "REJECTED"])
        self.assertIn("risk", body)
        self.assertIn("plan", body)
        # these two are @property on the dataclasses, not fields — dataclasses.asdict()
        # silently drops them, so assert explicitly that the API adds them back in.
        self.assertIn("risk_reward_ratio", body["plan"])
        self.assertIsInstance(body["plan"]["risk_reward_ratio"], (int, float))
        self.assertIn("status", body["risk"])
        self.assertIn(body["risk"]["status"], ["PROTECTED", "BLOCKED"])

    def test_fund_endpoint_scans_whole_watchlist(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/fund")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        entry_symbols = {e["symbol"] for e in body["entries"]}
        from aurum.datafeed.universe import DEFAULT_WATCHLIST

        self.assertEqual(entry_symbols, set(DEFAULT_WATCHLIST))
        for entry in body["entries"]:
            self.assertIsNotNone(entry["memo"])
            self.assertIn(entry["memo"]["verdict"], ["APPROVED", "WATCHLIST", "REJECTED"])
            self.assertIn("risk_reward_ratio", entry["memo"]["plan"])
            self.assertIn("status", entry["memo"]["risk"])

    def test_fund_endpoint_reports_partial_failure_without_crashing(self):
        def flaky_history(symbol, range_="10y", interval="1d"):
            if symbol == "SILVER":
                raise yahoo.DataFeedError("429 for silver")
            return _fake_history(symbol, range_, interval)

        with patch("aurum.datafeed.cache.get_history", side_effect=flaky_history):
            res = self.client.get("/api/fund")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        silver = next(e for e in body["entries"] if e["symbol"] == "SILVER")
        self.assertIsNone(silver["memo"])
        self.assertIn("429", silver["error"])
        gold = next(e for e in body["entries"] if e["symbol"] == "GOLD")
        self.assertIsNotNone(gold["memo"])

    def test_optimize_endpoint(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/optimize?method=hrp")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertAlmostEqual(sum(body["weights"].values()), 1.0, places=4)

    def test_optimize_endpoint_returns_clean_422_on_optimizer_failure(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history), \
             patch("aurum.optimize.engine.optimize", side_effect=ValueError("argmax of an empty sequence")):
            res = self.client.get("/api/optimize?method=hrp")
        self.assertEqual(res.status_code, 422)
        self.assertIn("Optimizer failed", res.json()["detail"])

    def test_backtest_endpoint_includes_benchmark_curve(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/backtest/GOLD")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("stats", body)
        self.assertGreater(len(body["strategy_equity_curve"]), 0)
        self.assertGreater(len(body["buy_hold_equity_curve"]), 0)

    def test_forecast_baseline_endpoint(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/forecast/baseline/GOLD?horizon=5")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["point_forecast"]), 5)


if __name__ == "__main__":
    unittest.main()
