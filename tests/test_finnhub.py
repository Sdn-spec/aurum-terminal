"""Parsing tests against Finnhub's documented response shapes (hand-built
here, no live key involved), plus the shared key resolution order."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import finnhub, provider

SAMPLE_NEWS_RESPONSE = [
    {"headline": "Company beats estimates", "source": "Reuters", "url": "https://example.com/1", "datetime": 1700000200},
    {"headline": "Company announces buyback", "source": "Bloomberg", "url": "https://example.com/2", "datetime": 1700000100},
    {"headline": "", "source": "Nowhere", "url": "https://example.com/3", "datetime": 1700000300},  # no headline, dropped
]

SAMPLE_EARNINGS_RESPONSE = {
    "earningsCalendar": [
        {"date": "2026-11-05", "epsEstimate": 1.42, "epsActual": None},
        {"date": "2027-02-10", "epsEstimate": 1.55, "epsActual": None},
    ]
}


class _FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestFinnhubParsing(unittest.TestCase):
    def test_get_company_news_drops_empty_headlines_and_sorts_newest_first(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_NEWS_RESPONSE)):
            items = finnhub.get_company_news("AAPL", "fake-key")
        self.assertEqual(len(items), 2)  # the blank-headline row is dropped
        self.assertEqual(items[0].headline, "Company beats estimates")  # datetime 1700000200, newest of the two valid rows

    def test_get_company_news_returns_empty_for_a_non_stock_symbol(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse([])):
            items = finnhub.get_company_news("GOLD", "fake-key")
        self.assertEqual(items, [])

    def test_get_next_earnings_returns_the_soonest_event(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_EARNINGS_RESPONSE)):
            event = finnhub.get_next_earnings("AAPL", "fake-key")
        self.assertEqual(event.date, "2026-11-05")
        self.assertEqual(event.eps_estimate, 1.42)

    def test_get_next_earnings_returns_none_when_nothing_scheduled(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse({"earningsCalendar": []})):
            event = finnhub.get_next_earnings("GOLD", "fake-key")
        self.assertIsNone(event)


class TestFinnhubApiKeyResolution(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config_patch = patch.object(provider, "CONFIG_PATH", Path(self._tmpdir.name) / "config.json")
        self._config_patch.start()
        self._env_patch = patch.dict("os.environ", {}, clear=False)
        self._env_patch.start()
        import os

        os.environ.pop("FINNHUB_API_KEY", None)

    def tearDown(self):
        self._config_patch.stop()
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_no_key_configured_returns_none(self):
        self.assertIsNone(finnhub.resolve_api_key())

    def test_env_var_takes_priority(self):
        provider.CONFIG_PATH.write_text(json.dumps({"finnhub_api_key": "from-config"}))
        with patch.dict("os.environ", {"FINNHUB_API_KEY": "from-env"}):
            self.assertEqual(finnhub.resolve_api_key(), "from-env")

    def test_falls_back_to_config_file(self):
        provider.CONFIG_PATH.write_text(json.dumps({"finnhub_api_key": "from-config"}))
        self.assertEqual(finnhub.resolve_api_key(), "from-config")


if __name__ == "__main__":
    unittest.main()
