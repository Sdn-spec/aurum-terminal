"""API-level tests for the FastAPI backend. Network calls are mocked —
these test the web layer's wiring and error handling, not Yahoo's uptime."""

import json
import os
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from aurum.datafeed import cache, finnhub, fred, provider, watchlist_store, yahoo
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
        # provider.get_quote/get_history fall back to Twelve Data whenever a key is
        # configured — and once you've actually set one up in data/config.json (as
        # intended, for real use), leaving this unpatched means "mock Yahoo failing"
        # tests silently make a real network call instead, flaking on whatever Twelve
        # Data happens to do at that moment. Point CONFIG_PATH at a tempdir with
        # nothing in it and clear the env var, so these tests are deterministic
        # regardless of what's actually configured on the machine running them.
        self._config_patch = patch.object(provider, "CONFIG_PATH", Path(self._tmpdir.name) / "config.json")
        self._config_patch.start()
        self._env_patch = patch.dict("os.environ", {}, clear=False)
        self._env_patch.start()
        os.environ.pop("TWELVEDATA_API_KEY", None)
        os.environ.pop("FRED_API_KEY", None)
        os.environ.pop("FINNHUB_API_KEY", None)
        # server._macro_cache/_news_cache are process-global — reset them per test
        # so one test's fake FRED/Finnhub data can't leak into the next.
        self._macro_cache_patch = patch.object(server, "_macro_cache", {"data": None, "at": 0.0})
        self._macro_cache_patch.start()
        self._news_cache_patch = patch.object(server, "_news_cache", {})
        self._news_cache_patch.start()
        # watchlist_store.load_watchlist() falls back to DEFAULT_WATCHLIST when
        # this file doesn't exist, which is exactly what a clean tempdir gives —
        # isolates these tests from any real customization on the machine running them.
        self._watchlist_patch = patch.object(watchlist_store, "WATCHLIST_PATH", Path(self._tmpdir.name) / "watchlist.json")
        self._watchlist_patch.start()

    def tearDown(self):
        self._state_patch.stop()
        self._cache_patch.stop()
        self._config_patch.stop()
        self._env_patch.stop()
        self._macro_cache_patch.stop()
        self._news_cache_patch.stop()
        self._watchlist_patch.stop()
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

    def test_watchlist_add(self):
        res = self.client.post("/api/watchlist", json={"name": "aapl"})
        self.assertEqual(res.status_code, 200)
        names = [row["name"] for row in res.json()]
        self.assertIn("AAPL", names)
        # persisted -- a fresh GET reflects it too, not just the POST response
        res = self.client.get("/api/watchlist")
        self.assertIn("AAPL", [row["name"] for row in res.json()])

    def test_watchlist_add_duplicate_returns_409(self):
        res = self.client.post("/api/watchlist", json={"name": "GOLD"})
        self.assertEqual(res.status_code, 409)

    def test_watchlist_add_empty_name_returns_422(self):
        res = self.client.post("/api/watchlist", json={"name": "   "})
        self.assertEqual(res.status_code, 422)

    def test_watchlist_delete(self):
        res = self.client.delete("/api/watchlist/SILVER")
        self.assertEqual(res.status_code, 200)
        names = [row["name"] for row in res.json()]
        self.assertNotIn("SILVER", names)

    def test_watchlist_delete_unknown_symbol_returns_404(self):
        res = self.client.delete("/api/watchlist/NOTREAL")
        self.assertEqual(res.status_code, 404)

    def test_watchlist_rename(self):
        res = self.client.put("/api/watchlist/SILVER", json={"name": "platinum"})
        self.assertEqual(res.status_code, 200)
        names = [row["name"] for row in res.json()]
        self.assertIn("PLATINUM", names)
        self.assertNotIn("SILVER", names)

    def test_watchlist_rename_unknown_symbol_returns_404(self):
        res = self.client.put("/api/watchlist/NOTREAL", json={"name": "AAPL"})
        self.assertEqual(res.status_code, 404)

    def test_watchlist_rename_to_existing_symbol_returns_409(self):
        res = self.client.put("/api/watchlist/SILVER", json={"name": "gold"})
        self.assertEqual(res.status_code, 409)

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

    def test_fund_endpoint_respects_a_customized_watchlist(self):
        self.client.delete("/api/watchlist/SILVER")
        self.client.post("/api/watchlist", json={"name": "AAPL"})
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/fund")
        entry_symbols = {e["symbol"] for e in res.json()["entries"]}
        self.assertIn("AAPL", entry_symbols)
        self.assertNotIn("SILVER", entry_symbols)

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

    def test_analyze_endpoint_returns_full_report(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/analyze/GOLD")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["symbol"], "GOLD")
        self.assertIn(body["verdict"], ["INVEST", "WATCH", "AVOID"])
        self.assertIn(body["confidence"], ["High", "Medium", "Low"])
        self.assertIn("status", body["risk"])  # @property gap, same fix as the decision memo
        for horizon in ("day_trade", "long_term"):
            plan = body[horizon]
            self.assertIn(plan["direction"], ["long", "short"])
            self.assertIn("take_profit_1", plan)
            self.assertIn("take_profit_2", plan)
        self.assertGreater(len(body["debate"]["bull_points"]) + len(body["debate"]["bear_points"]), 0)
        self.assertIn("trend_regime", body["research"])

    def test_analyze_endpoint_works_for_a_raw_ticker_not_in_the_watchlist(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/analyze/AAPL")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["symbol"], "AAPL")

    def test_analyze_endpoint_has_empty_macro_news_earnings_when_no_keys_configured(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/analyze/GOLD")
        body = res.json()
        self.assertEqual(body["macro"], [])
        self.assertEqual(body["news"], [])
        self.assertIsNone(body["earnings"])

    def test_analyze_endpoint_folds_in_macro_and_news_when_keys_are_configured(self):
        provider.CONFIG_PATH.write_text(json.dumps({"fred_api_key": "fake-fred", "finnhub_api_key": "fake-finnhub"}))
        fake_macro = [fred.MacroSeries(key="fed_funds_rate", series_id="DFF", label="Fed funds rate", latest_date="2026-08-01", latest_value=4.33, previous_value=4.58)]
        fake_news = [finnhub.NewsItem(headline="Gold rallies", source="Reuters", url="https://example.com", published=1700000000)]
        fake_earnings = finnhub.EarningsEvent(date="2026-11-05", eps_estimate=1.42, eps_actual=None)
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history), \
             patch("aurum.datafeed.fred.get_macro_snapshot", return_value=fake_macro), \
             patch("aurum.datafeed.finnhub.get_company_news", return_value=fake_news), \
             patch("aurum.datafeed.finnhub.get_next_earnings", return_value=fake_earnings):
            res = self.client.get("/api/analyze/GOLD")
        body = res.json()
        self.assertEqual(body["macro"][0]["key"], "fed_funds_rate")
        self.assertEqual(body["news"][0]["headline"], "Gold rallies")
        self.assertEqual(body["earnings"]["date"], "2026-11-05")

    def test_macro_endpoint_returns_empty_list_when_no_key_configured(self):
        res = self.client.get("/api/macro")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

    def test_macro_endpoint_returns_snapshot_when_key_configured(self):
        provider.CONFIG_PATH.write_text(json.dumps({"fred_api_key": "fake-fred"}))
        fake_macro = [fred.MacroSeries(key="unemployment", series_id="UNRATE", label="Unemployment rate", latest_date="2026-08-01", latest_value=4.1, previous_value=4.0)]
        with patch("aurum.datafeed.fred.get_macro_snapshot", return_value=fake_macro):
            res = self.client.get("/api/macro")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body[0]["key"], "unemployment")
        self.assertEqual(body[0]["latest_value"], 4.1)

    def test_forecast_baseline_endpoint(self):
        with patch("aurum.datafeed.cache.get_history", side_effect=_fake_history):
            res = self.client.get("/api/forecast/baseline/GOLD?horizon=5")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["point_forecast"]), 5)


if __name__ == "__main__":
    unittest.main()
