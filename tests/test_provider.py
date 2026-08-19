import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import provider, yahoo

SAMPLE_QUOTE = yahoo.Quote("GC=F", 2410.5, 2415.0, 2402.0, 2500.0, 1900.0, "USD", "COMEX", 1700000000)
SAMPLE_BARS = [yahoo.HistoryBar(1700000000, 2400.0, 2410.0, 2395.0, 2405.0, 1000.0)]


class TestProvider(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config_patch = patch.object(provider, "CONFIG_PATH", Path(self._tmpdir.name) / "config.json")
        self._config_patch.start()

    def tearDown(self):
        self._config_patch.stop()
        self._tmpdir.cleanup()

    def test_yahoo_success_never_touches_fallback(self):
        with patch("aurum.datafeed.yahoo.get_quote", return_value=SAMPLE_QUOTE), \
             patch("aurum.datafeed.twelvedata.get_quote") as mock_td:
            quote = provider.get_quote("GOLD")
        self.assertEqual(quote.price, 2410.5)
        mock_td.assert_not_called()

    def test_yahoo_failure_with_no_key_configured_raises_yahoo_error(self):
        with patch("aurum.datafeed.yahoo.get_quote", side_effect=yahoo.DataFeedError("429")), \
             patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(yahoo.DataFeedError) as ctx:
                provider.get_quote("GOLD")
        self.assertIn("429", str(ctx.exception))

    def test_yahoo_failure_with_key_falls_back_to_twelvedata(self):
        with patch("aurum.datafeed.yahoo.get_quote", side_effect=yahoo.DataFeedError("429")), \
             patch("aurum.datafeed.twelvedata.get_quote", return_value=SAMPLE_QUOTE) as mock_td, \
             patch.dict("os.environ", {"TWELVEDATA_API_KEY": "fake-key"}):
            quote = provider.get_quote("GOLD")
        self.assertEqual(quote.price, 2410.5)
        mock_td.assert_called_once()
        self.assertEqual(mock_td.call_args[0][0], "XAU/USD")  # resolved via TWELVEDATA_ALIASES
        self.assertEqual(mock_td.call_args[0][1], "fake-key")

    def test_both_providers_failing_raises_the_original_yahoo_error(self):
        with patch("aurum.datafeed.yahoo.get_quote", side_effect=yahoo.DataFeedError("yahoo 429")), \
             patch("aurum.datafeed.twelvedata.get_quote", side_effect=yahoo.DataFeedError("twelvedata down")), \
             patch.dict("os.environ", {"TWELVEDATA_API_KEY": "fake-key"}):
            with self.assertRaises(yahoo.DataFeedError) as ctx:
                provider.get_quote("GOLD")
        self.assertIn("yahoo 429", str(ctx.exception))  # the more familiar/actionable error, not the fallback's

    def test_history_fallback_uses_twelvedata_symbol_and_history(self):
        with patch("aurum.datafeed.yahoo.get_history", side_effect=yahoo.DataFeedError("429")), \
             patch("aurum.datafeed.twelvedata.get_history", return_value=SAMPLE_BARS) as mock_td, \
             patch.dict("os.environ", {"TWELVEDATA_API_KEY": "fake-key"}):
            bars = provider.get_history("GOLD", range_="10y", interval="1d")
        self.assertEqual(bars, SAMPLE_BARS)
        mock_td.assert_called_once()
        self.assertEqual(mock_td.call_args[0][0], "XAU/USD")

    def test_key_can_come_from_config_file_instead_of_env(self):
        (Path(self._tmpdir.name) / "config.json").write_text('{"twelvedata_api_key": "file-key"}')
        with patch("aurum.datafeed.yahoo.get_quote", side_effect=yahoo.DataFeedError("429")), \
             patch("aurum.datafeed.twelvedata.get_quote", return_value=SAMPLE_QUOTE) as mock_td, \
             patch.dict("os.environ", {}, clear=True):
            provider.get_quote("GOLD")
        self.assertEqual(mock_td.call_args[0][1], "file-key")


if __name__ == "__main__":
    unittest.main()
