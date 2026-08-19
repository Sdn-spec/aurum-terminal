"""Tests for the Yahoo chart API client, using a mocked HTTP layer — no
real network needed, and these stay deterministic regardless of Yahoo's
current rate-limit state (which is real and does happen, see broker retry
test below)."""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import yahoo

SAMPLE_CHART_RESPONSE = {
    "chart": {
        "result": [
            {
                "meta": {
                    "currency": "USD",
                    "symbol": "GC=F",
                    "exchangeName": "CMX",
                    "fullExchangeName": "COMEX",
                    "regularMarketPrice": 2410.5,
                    "regularMarketDayHigh": 2415.0,
                    "regularMarketDayLow": 2402.0,
                    "fiftyTwoWeekHigh": 2500.0,
                    "fiftyTwoWeekLow": 1900.0,
                    "regularMarketTime": 1700000000,
                },
                "timestamp": [1699900000, 1699986400, 1700072800],
                "indicators": {
                    "quote": [
                        {
                            "open": [2400.0, 2405.0, None],
                            "high": [2410.0, 2412.0, None],
                            "low": [2395.0, 2400.0, None],
                            "close": [2405.0, 2408.0, None],
                            "volume": [1000, 1200, None],
                        }
                    ]
                },
            }
        ],
        "error": None,
    }
}


class _FakeResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestYahooClient(unittest.TestCase):
    def test_get_history_parses_bars_and_skips_nulls(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_CHART_RESPONSE)):
            bars = yahoo.get_history("GC=F", range_="5d", interval="1d")
        self.assertEqual(len(bars), 2)  # the None-padded 3rd bar is skipped
        self.assertEqual(bars[0].close, 2405.0)
        self.assertEqual(bars[1].volume, 1200)

    def test_get_quote_reads_meta_block(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_CHART_RESPONSE)):
            quote = yahoo.get_quote("GC=F")
        self.assertEqual(quote.price, 2410.5)
        self.assertEqual(quote.day_high, 2415.0)
        self.assertEqual(quote.exchange, "COMEX")

    def test_429_retries_then_succeeds(self):
        responses = [
            urllib.error.HTTPError("url", 429, "Too Many Requests", {}, io.BytesIO()),
            _FakeResponse(SAMPLE_CHART_RESPONSE),
        ]

        def side_effect(*args, **kwargs):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch("urllib.request.urlopen", side_effect=side_effect), patch("time.sleep"):
            bars = yahoo.get_history("GC=F")
        self.assertEqual(len(bars), 2)

    def test_429_exhausts_retries_and_raises_datafeed_error(self):
        def always_429(*args, **kwargs):
            raise urllib.error.HTTPError("url", 429, "Too Many Requests", {}, io.BytesIO())

        with patch("urllib.request.urlopen", side_effect=always_429), patch("time.sleep"):
            with self.assertRaises(yahoo.DataFeedError):
                yahoo.get_history("GC=F")

    def test_unknown_symbol_raises_datafeed_error(self):
        empty = {"chart": {"result": None, "error": {"description": "No data found"}}}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(empty)):
            with self.assertRaises(yahoo.DataFeedError):
                yahoo.get_history("NOT_A_REAL_TICKER")


if __name__ == "__main__":
    unittest.main()
