import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import altsources, markets
from aurum.datafeed.yahoo import DataFeedError

NSE_PAYLOAD = {
    "data": [
        {"index": "NIFTY 50", "last": "24252", "previousClose": "24231.85", "variation": "20.15",
         "percentChange": "0.08", "high": "24300", "low": "24200",
         "perChange30d": "0.27", "perChange365d": "-3.32"},
        {"index": "NIFTY BANK", "last": "57761.95", "previousClose": "57495.9", "variation": "266.05",
         "percentChange": "0.46", "high": "57900", "low": "57400",
         "perChange30d": "-0.13", "perChange365d": "3.60"},
        {"index": "INDIA VIX", "last": "11.2", "previousClose": "10.76", "variation": "0.44",
         "percentChange": "4.09", "perChange30d": "-11.11", "perChange365d": "-1.50"},
    ]
}


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestNumberParsing(unittest.TestCase):
    def test_strips_thousands_separators(self):
        self.assertEqual(altsources._num("1,234.5"), 1234.5)

    def test_blank_and_none_become_none(self):
        self.assertIsNone(altsources._num(""))
        self.assertIsNone(altsources._num(None))
        self.assertIsNone(altsources._num("-"))


class TestNseIndices(unittest.TestCase):
    def setUp(self):
        altsources.reset_nse_session()

    def tearDown(self):
        altsources.reset_nse_session()

    def test_parses_indices_including_the_1m_and_1y_columns(self):
        """NSE publishes 30-day and 1-year moves directly, so the board's
        1M/1Y columns cost nothing extra for Indian indices."""
        with patch("urllib.request.OpenerDirector.open", return_value=_Resp(NSE_PAYLOAD)), \
             patch("time.sleep"):
            out = altsources.fetch_nse_indices()
        self.assertIn("NIFTY 50", out)
        nifty = out["NIFTY 50"]
        self.assertEqual(nifty["price"], 24252.0)
        self.assertEqual(nifty["change_pct"], 0.08)
        self.assertEqual(nifty["change_1m_pct"], 0.27)
        self.assertEqual(nifty["change_1y_pct"], -3.32)
        self.assertEqual(nifty["currency"], "INR")

    def test_empty_payload_raises(self):
        with patch("urllib.request.OpenerDirector.open", return_value=_Resp({"data": []})), \
             patch("time.sleep"):
            with self.assertRaises(DataFeedError):
                altsources.fetch_nse_indices()


class TestPerSourceCache(unittest.TestCase):
    def setUp(self):
        altsources.clear_cache()

    def tearDown(self):
        altsources.clear_cache()

    def test_repeat_calls_inside_the_ttl_do_not_refetch(self):
        """Finnhub is one request per symbol against a 60/min free tier, and
        the board ticks every 5s — without this it was ~200 calls a minute
        and rows flickered blank as Finnhub started refusing."""
        calls = []

        def producer():
            calls.append(1)
            return {"SPY": {"price": 764.0}}

        for _ in range(6):
            altsources._cached("finnhub", producer)
        self.assertEqual(len(calls), 1)

    def test_expired_ttl_refetches(self):
        calls = []

        def producer():
            calls.append(1)
            return {"x": 1}

        altsources._cached("crypto", producer)
        with patch.object(altsources, "_CACHE_TTL", {"crypto": -1}):
            altsources._cached("crypto", producer)
        self.assertEqual(len(calls), 2)

    def test_a_failed_refresh_keeps_serving_the_last_good_value(self):
        altsources._cached("nse", lambda: {"NIFTY 50": {"price": 24252.0}})
        with patch.object(altsources, "_CACHE_TTL", {"nse": -1}):
            out = altsources._cached("nse", lambda: (_ for _ in ()).throw(DataFeedError("down")))
        self.assertEqual(out["NIFTY 50"]["price"], 24252.0)

    def test_failure_with_nothing_cached_still_raises(self):
        with self.assertRaises(DataFeedError):
            altsources._cached("nse", lambda: (_ for _ in ()).throw(DataFeedError("down")))


class TestBoardFromAltSources(unittest.TestCase):
    def _patch_all(self, nse=None, crypto=None, fx=None, proxies=None):
        def maybe(value):
            if value is None:
                return patch.object(altsources, "x", create=True)
            return value

        return (
            patch.object(altsources, "fetch_nse_indices",
                         side_effect=DataFeedError("down") if nse is None else (lambda: nse)),
            patch.object(altsources, "fetch_crypto",
                         side_effect=DataFeedError("down") if crypto is None else (lambda *a, **k: crypto)),
            patch.object(altsources, "fetch_fx",
                         side_effect=DataFeedError("down") if fx is None else (lambda *a, **k: fx)),
            patch.object(altsources, "fetch_finnhub_quotes",
                         side_effect=DataFeedError("down") if proxies is None else (lambda *a, **k: proxies)),
        )

    def test_nse_fills_the_india_rows(self):
        nse = {
            "NIFTY 50": {"price": 24252.0, "change_pct": 0.08, "currency": "INR",
                         "change_1m_pct": 0.27, "change_1y_pct": -3.32},
            "NIFTY BANK": {"price": 57761.95, "change_pct": 0.46, "currency": "INR"},
        }
        a, b, c, d = self._patch_all(nse=nse)
        with a, b, c, d:
            rows = markets.fetch_board_from_alt_sources()
        by_code = {r.code: r for r in rows}
        self.assertEqual(by_code["NIFTY"].price, 24252.0)
        self.assertEqual(by_code["NIFTY"].source, "NSE India")
        self.assertFalse(by_code["NIFTY"].is_proxy)
        self.assertEqual(by_code["NIFTY"].change_1m_pct, 0.27)
        self.assertEqual(by_code["BANKNIFTY"].price, 57761.95)

    def test_etf_rows_are_flagged_as_proxies(self):
        """An ETF is not the index: SPY trades near 765 while SPX is near
        7,650. The row has to say so."""
        a, b, c, d = self._patch_all(proxies={"SPY": {"price": 764.58, "change_pct": 0.26, "currency": "USD"}})
        with a, b, c, d:
            rows = markets.fetch_board_from_alt_sources()
        spx = next(r for r in rows if r.code == "SPX")
        self.assertEqual(spx.price, 764.58)
        self.assertTrue(spx.is_proxy)
        self.assertEqual(spx.proxy_symbol, "SPY")
        self.assertEqual(spx.source, "Finnhub")

    def test_one_source_failing_does_not_cost_the_others(self):
        a, b, c, d = self._patch_all(crypto={"bitcoin": {"price": 77609.0, "change_pct": 8.26, "currency": "USD"}})
        with a, b, c, d:
            rows = markets.fetch_board_from_alt_sources()
        btc = next(r for r in rows if r.code == "BTC")
        self.assertEqual(btc.price, 77609.0)
        self.assertEqual(btc.source, "CoinGecko")

    def test_raises_only_when_nothing_at_all_could_be_filled(self):
        a, b, c, d = self._patch_all()
        with a, b, c, d:
            with self.assertRaises(DataFeedError):
                markets.fetch_board_from_alt_sources()

    def test_every_row_is_still_present_even_when_unpriced(self):
        a, b, c, d = self._patch_all(fx={"INR": 95.71})
        with a, b, c, d:
            rows = markets.fetch_board_from_alt_sources()
        self.assertEqual(len(rows), len(markets.INDICES))


class TestMergeBoards(unittest.TestCase):
    def _row(self, code, price=None, source=""):
        return markets.MarketRow(code=code, label=code, ticker=code, region="Americas",
                                 price=price, source=source)

    def test_gaps_are_filled_from_the_secondary_board(self):
        primary = [self._row("SPX", None), self._row("INDU", 52000.0, "Yahoo")]
        secondary = [self._row("SPX", 764.58, "Finnhub"), self._row("INDU", 530.0, "Finnhub")]
        merged = markets.merge_boards(primary, secondary)
        by_code = {r.code: r for r in merged}
        self.assertEqual(by_code["SPX"].price, 764.58)  # gap filled

    def test_a_real_quote_is_never_replaced_by_a_proxy(self):
        """Yahoo answering for some symbols and not others is the normal case
        while it throttles; the real index level must win where it exists."""
        primary = [self._row("INDU", 52000.0, "Yahoo")]
        secondary = [self._row("INDU", 530.0, "Finnhub")]
        merged = markets.merge_boards(primary, secondary)
        self.assertEqual(merged[0].price, 52000.0)
        self.assertEqual(merged[0].source, "Yahoo")


if __name__ == "__main__":
    unittest.main()
