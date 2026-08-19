"""API-level tests for the FastAPI backend. Network calls are mocked —
these test the web layer's wiring and error handling, not Yahoo's uptime."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from aurum.datafeed import yahoo
from aurum.web import server


def _fake_history(symbol, range_="10y", interval="1d"):
    import random

    rng = random.Random(hash(symbol) % (2**32))
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

    def tearDown(self):
        self._state_patch.stop()
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

    def test_optimize_endpoint(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/optimize?method=hrp")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertAlmostEqual(sum(body["weights"].values()), 1.0, places=4)

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
