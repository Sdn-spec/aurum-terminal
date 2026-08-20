import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import markets
from aurum.datafeed.yahoo import DataFeedError, HistoryBar


def _bars(closes):
    return [HistoryBar(1_700_000_000 + i * 86400, c, c, c, c, 1000.0) for i, c in enumerate(closes)]


class TestIndexUniverse(unittest.TestCase):
    def test_every_index_has_a_known_region(self):
        for idx in markets.INDICES:
            self.assertIn(idx.region, markets.REGIONS, f"{idx.code} has an unlisted region")

    def test_codes_and_tickers_are_unique(self):
        codes = [i.code for i in markets.INDICES]
        tickers = [i.ticker for i in markets.INDICES]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(len(tickers), len(set(tickers)))

    def test_every_region_has_at_least_one_index(self):
        covered = {i.region for i in markets.INDICES}
        self.assertEqual(covered, set(markets.REGIONS))


class TestComputeHistoryStats(unittest.TestCase):
    def test_returns_empty_stats_for_no_bars(self):
        stats = markets.compute_history_stats([])
        self.assertIsNone(stats["change_1m_pct"])
        self.assertIsNone(stats["change_1y_pct"])
        self.assertEqual(stats["spark"], [])

    def test_one_month_change_is_measured_21_bars_back(self):
        closes = [100.0] * 21 + [110.0]  # 22 bars: index -22 is 100, last is 110
        stats = markets.compute_history_stats(_bars(closes))
        self.assertAlmostEqual(stats["change_1m_pct"], 10.0)

    def test_one_year_change_is_none_without_enough_history(self):
        stats = markets.compute_history_stats(_bars([100.0] * 50))
        self.assertIsNone(stats["change_1y_pct"])

    def test_one_year_change_is_computed_with_enough_history(self):
        closes = [100.0] * 251 + [125.0]
        stats = markets.compute_history_stats(_bars(closes))
        self.assertAlmostEqual(stats["change_1y_pct"], 25.0)

    def test_spark_is_capped_at_30_points(self):
        stats = markets.compute_history_stats(_bars([float(i) for i in range(1, 101)]))
        self.assertEqual(len(stats["spark"]), 30)
        self.assertEqual(stats["spark"][-1], 100.0)


class TestFetchBoard(unittest.TestCase):
    def test_maps_quotes_onto_the_board_in_index_order(self):
        fake = [
            {
                "symbol": "^GSPC", "regularMarketPrice": 5000.0, "regularMarketPreviousClose": 4950.0,
                "regularMarketChange": 50.0, "regularMarketChangePercent": 1.0101,
                "currency": "USD", "regularMarketTime": 1_700_000_000, "marketState": "REGULAR",
            }
        ]
        with patch.object(markets._session, "fetch_quotes", return_value=fake):
            rows = markets.fetch_board()
        self.assertEqual(len(rows), len(markets.INDICES))
        self.assertEqual([r.code for r in rows], [i.code for i in markets.INDICES])
        spx = next(r for r in rows if r.ticker == "^GSPC")
        self.assertEqual(spx.price, 5000.0)
        self.assertAlmostEqual(spx.change_pct, 1.0101)
        self.assertEqual(spx.currency, "USD")

    def test_symbols_missing_from_the_response_still_appear_with_null_price(self):
        with patch.object(markets._session, "fetch_quotes", return_value=[]):
            rows = markets.fetch_board()
        self.assertEqual(len(rows), len(markets.INDICES))
        self.assertTrue(all(r.price is None for r in rows))

    def test_change_is_derived_when_yahoo_omits_it(self):
        fake = [{"symbol": "^GSPC", "regularMarketPrice": 110.0, "regularMarketPreviousClose": 100.0}]
        with patch.object(markets._session, "fetch_quotes", return_value=fake):
            rows = markets.fetch_board()
        spx = next(r for r in rows if r.ticker == "^GSPC")
        self.assertAlmostEqual(spx.change, 10.0)
        self.assertAlmostEqual(spx.change_pct, 10.0)

    def test_upstream_failure_propagates_as_datafeed_error(self):
        with patch.object(markets._session, "fetch_quotes", side_effect=DataFeedError("nope")):
            with self.assertRaises(DataFeedError):
                markets.fetch_board()


class TestFetchBoardViaQuoteCache(unittest.TestCase):
    def _quote(self, price, prev):
        from aurum.datafeed.yahoo import Quote
        return Quote("X", price, 0.0, 0.0, 0.0, 0.0, "USD", "TEST", 1_700_000_000, previous_close=prev)

    def test_builds_the_whole_board_from_per_symbol_quotes(self):
        rows = markets.fetch_board_via_quote_cache(lambda t: self._quote(110.0, 100.0))
        self.assertEqual(len(rows), len(markets.INDICES))
        self.assertAlmostEqual(rows[0].change_pct, 10.0)
        self.assertAlmostEqual(rows[0].change, 10.0)

    def test_one_failing_symbol_does_not_blank_the_board(self):
        def flaky(ticker):
            if ticker == markets.INDICES[0].ticker:
                raise DataFeedError("no data")
            return self._quote(110.0, 100.0)

        rows = markets.fetch_board_via_quote_cache(flaky)
        self.assertEqual(len(rows), len(markets.INDICES))
        self.assertIsNone(rows[0].price)
        self.assertIsNotNone(rows[1].price)

    def test_raises_only_when_every_symbol_fails(self):
        def always_fail(ticker):
            raise DataFeedError("no data")

        with self.assertRaises(DataFeedError):
            markets.fetch_board_via_quote_cache(always_fail)


class TestYahooSessionCrumb(unittest.TestCase):
    def test_html_error_page_is_not_accepted_as_a_crumb(self):
        session = markets._YahooSession()

        class FakeResp:
            def __init__(self, body):
                self._b = body
            def read(self, *a):
                return self._b
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with patch("urllib.request.OpenerDirector.open", return_value=FakeResp(b"<!DOCTYPE html><html>error</html>")):
            session._handshake()
        self.assertIsNone(session._crumb)

    def test_short_token_is_accepted_as_a_crumb(self):
        session = markets._YahooSession()

        class FakeResp:
            def read(self, *a):
                return b"abc123XYZ"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with patch("urllib.request.OpenerDirector.open", return_value=FakeResp()):
            session._handshake()
        self.assertEqual(session._crumb, "abc123XYZ")


if __name__ == "__main__":
    unittest.main()
