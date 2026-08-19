"""Parsing tests against Twelve Data's documented response shape (hand-built
here, no live key involved) — these validate the parsing logic is correct,
not that any specific symbol resolves on the free tier."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import twelvedata
from aurum.datafeed.yahoo import DataFeedError

SAMPLE_QUOTE_RESPONSE = {
    "symbol": "XAU/USD",
    "name": "Gold Spot",
    "exchange": "Forex",
    "currency": "USD",
    "datetime": "2024-01-01",
    "timestamp": 1704067200,
    "open": "2062.90",
    "high": "2072.30",
    "low": "2055.70",
    "close": "2062.30",
    "previous_close": "2071.90",
    "fifty_two_week": {"low": "1804.40", "high": "2152.30"},
}

SAMPLE_TIME_SERIES_RESPONSE = {
    "meta": {"symbol": "XAU/USD", "interval": "1day"},
    "values": [
        {"datetime": "2024-01-03", "open": "2065.00", "high": "2070.00", "low": "2060.00", "close": "2068.00", "volume": "0"},
        {"datetime": "2024-01-02", "open": "2062.00", "high": "2066.00", "low": "2058.00", "close": "2065.00", "volume": "0"},
        {"datetime": "2024-01-01", "open": "2062.90", "high": "2072.30", "low": "2055.70", "close": "2062.30", "volume": "0"},
    ],
    "status": "ok",
}

SAMPLE_ERROR_RESPONSE = {"code": 400, "message": "**symbol** parameter is missing or invalid", "status": "error"}


class _FakeResponse:
    def __init__(self, payload):
        import json

        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestTwelveData(unittest.TestCase):
    def test_get_quote_parses_documented_shape(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_QUOTE_RESPONSE)):
            quote = twelvedata.get_quote("XAU/USD", "fake-key")
        self.assertEqual(quote.price, 2062.30)
        self.assertEqual(quote.day_high, 2072.30)
        self.assertEqual(quote.fifty_two_week_low, 1804.40)
        self.assertEqual(quote.currency, "USD")

    def test_get_history_parses_and_sorts_ascending(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_TIME_SERIES_RESPONSE)):
            bars = twelvedata.get_history("XAU/USD", "fake-key")
        self.assertEqual(len(bars), 3)
        # Twelve Data returns newest-first; get_history must hand back ascending
        self.assertLess(bars[0].timestamp, bars[1].timestamp)
        self.assertLess(bars[1].timestamp, bars[2].timestamp)
        self.assertEqual(bars[0].close, 2062.30)  # 2024-01-01, the oldest
        self.assertEqual(bars[-1].close, 2068.00)  # 2024-01-03, the newest

    def test_error_response_raises_datafeed_error(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_ERROR_RESPONSE)):
            with self.assertRaises(DataFeedError):
                twelvedata.get_quote("NOTREAL", "fake-key")


if __name__ == "__main__":
    unittest.main()
