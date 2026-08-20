import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import cache, provider, yahoo

SAMPLE_BARS = [
    yahoo.HistoryBar(1700000000, 2400.0, 2410.0, 2395.0, 2405.0, 1000.0),
    yahoo.HistoryBar(1700086400, 2405.0, 2412.0, 2400.0, 2408.0, 1200.0),
]
SAMPLE_QUOTE = yahoo.Quote("GC=F", 2410.5, 2415.0, 2402.0, 2500.0, 1900.0, "USD", "COMEX", 1700000000)


class TestCache(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir.name))
        self._patch.start()
        # cache.get_history/get_quote call provider.get_*, which falls back to
        # Twelve Data whenever a key is configured — without isolating this, a
        # mocked "yahoo failed" test would make a real network call using
        # whatever key is actually sitting in data/config.json on this machine.
        self._config_patch = patch.object(provider, "CONFIG_PATH", Path(self._tmpdir.name) / "config.json")
        self._config_patch.start()
        self._env_patch = patch.dict("os.environ", {}, clear=False)
        self._env_patch.start()
        os.environ.pop("TWELVEDATA_API_KEY", None)

    def tearDown(self):
        self._patch.stop()
        self._config_patch.stop()
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_cache_miss_fetches_and_writes_file(self):
        with patch("aurum.datafeed.yahoo.get_history", return_value=SAMPLE_BARS) as mock_fetch:
            bars = cache.get_history("GC=F", range_="max", interval="1d")
        self.assertEqual(bars, SAMPLE_BARS)
        mock_fetch.assert_called_once()
        self.assertTrue(cache._cache_path("GC=F", "1d", "max").exists())

    def test_cache_hit_does_not_refetch(self):
        with patch("aurum.datafeed.yahoo.get_history", return_value=SAMPLE_BARS):
            cache.get_history("GC=F", range_="max", interval="1d")

        with patch("aurum.datafeed.yahoo.get_history") as mock_fetch:
            bars = cache.get_history("GC=F", range_="max", interval="1d")
        mock_fetch.assert_not_called()
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].close, 2405.0)

    def test_stale_cache_refetches(self):
        with patch("aurum.datafeed.yahoo.get_history", return_value=SAMPLE_BARS):
            cache.get_history("GC=F", range_="max", interval="1d")

        with patch("aurum.datafeed.yahoo.get_history", return_value=SAMPLE_BARS) as mock_fetch:
            cache.get_history("GC=F", range_="max", interval="1d", max_age_hours=-1)  # force staleness
        mock_fetch.assert_called_once()

    def test_empty_fetch_falls_back_to_stale_cache(self):
        with patch("aurum.datafeed.yahoo.get_history", return_value=SAMPLE_BARS):
            cache.get_history("GC=F", range_="max", interval="1d")

        with patch("aurum.datafeed.yahoo.get_history", return_value=[]):
            bars = cache.get_history("GC=F", range_="max", interval="1d", max_age_hours=-1)
        self.assertEqual(len(bars), 2)  # got the old data back instead of an empty result

    def test_failed_refetch_falls_back_to_stale_cache_instead_of_raising(self):
        with patch("aurum.datafeed.yahoo.get_history", return_value=SAMPLE_BARS):
            cache.get_history("GC=F", range_="max", interval="1d")

        with patch("aurum.datafeed.yahoo.get_history", side_effect=yahoo.DataFeedError("429")):
            bars = cache.get_history("GC=F", range_="max", interval="1d", max_age_hours=-1)
        self.assertEqual(len(bars), 2)  # a 429 on refresh still returns the last good data

    def test_failed_fetch_with_no_cache_at_all_still_raises(self):
        with patch("aurum.datafeed.yahoo.get_history", side_effect=yahoo.DataFeedError("429")):
            with self.assertRaises(yahoo.DataFeedError):
                cache.get_history("GC=F", range_="max", interval="1d")

    def test_concurrent_history_requests_only_fetch_once(self):
        call_count = {"n": 0}

        def slow_fetch(*args, **kwargs):
            call_count["n"] += 1
            time.sleep(0.05)
            return SAMPLE_BARS

        with patch("aurum.datafeed.yahoo.get_history", side_effect=slow_fetch):
            threads = [
                threading.Thread(target=cache.get_history, args=("GC=F",), kwargs={"range_": "max", "interval": "1d"})
                for _ in range(5)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(call_count["n"], 1)  # 5 concurrent callers, only 1 real fetch

    def test_quote_cache_hit_does_not_refetch(self):
        with patch("aurum.datafeed.yahoo.get_quote", return_value=SAMPLE_QUOTE):
            cache.get_quote("GC=F")
        with patch("aurum.datafeed.yahoo.get_quote") as mock_fetch:
            quote = cache.get_quote("GC=F")
        mock_fetch.assert_not_called()
        self.assertEqual(quote.price, 2410.5)

    def test_quote_cache_expires_after_max_age(self):
        with patch("aurum.datafeed.yahoo.get_quote", return_value=SAMPLE_QUOTE):
            cache.get_quote("GC=F")
        with patch("aurum.datafeed.yahoo.get_quote", return_value=SAMPLE_QUOTE) as mock_fetch:
            cache.get_quote("GC=F", max_age_seconds=-1)
        mock_fetch.assert_called_once()

    def test_quote_failed_refetch_falls_back_to_stale_quote(self):
        with patch("aurum.datafeed.yahoo.get_quote", return_value=SAMPLE_QUOTE):
            cache.get_quote("GC=F")
        with patch("aurum.datafeed.yahoo.get_quote", side_effect=yahoo.DataFeedError("429")):
            quote = cache.get_quote("GC=F", max_age_seconds=-1)
        self.assertEqual(quote.price, 2410.5)

    def test_concurrent_quote_requests_only_fetch_once(self):
        call_count = {"n": 0}

        def slow_fetch(*args, **kwargs):
            call_count["n"] += 1
            time.sleep(0.05)
            return SAMPLE_QUOTE

        with patch("aurum.datafeed.yahoo.get_quote", side_effect=slow_fetch):
            threads = [threading.Thread(target=cache.get_quote, args=("GC=F",)) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(call_count["n"], 1)


if __name__ == "__main__":
    unittest.main()
