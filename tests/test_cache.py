import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import cache, yahoo

SAMPLE_BARS = [
    yahoo.HistoryBar(1700000000, 2400.0, 2410.0, 2395.0, 2405.0, 1000.0),
    yahoo.HistoryBar(1700086400, 2405.0, 2412.0, 2400.0, 2408.0, 1200.0),
]


class TestCache(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patch = patch.object(cache, "CACHE_DIR", Path(self._tmpdir.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
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


if __name__ == "__main__":
    unittest.main()
