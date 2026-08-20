"""Parsing tests against FRED's documented response shape (hand-built here,
no live key involved), plus the env-var/config.json key resolution order
already established for the other providers."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import fred, provider
from aurum.datafeed.yahoo import DataFeedError

SAMPLE_OBSERVATIONS_RESPONSE = {
    "observations": [
        {"date": "2026-08-01", "value": "4.33"},
        {"date": "2026-07-01", "value": "4.58"},
        {"date": "2026-06-01", "value": "."},  # a missing reading, dropped
        {"date": "2026-05-01", "value": "5.10"},
    ]
}

SAMPLE_ERROR_RESPONSE = {"error_code": 400, "error_message": "The value for variable series_id is not a valid series."}


class _FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestFredParsing(unittest.TestCase):
    def test_get_series_drops_missing_values_and_sorts_oldest_first(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_OBSERVATIONS_RESPONSE)):
            observations = fred.get_series("DFF", "fake-key")
        self.assertEqual(len(observations), 3)  # the "." reading is dropped
        self.assertEqual(observations[0], ("2026-05-01", 5.10))
        self.assertEqual(observations[-1], ("2026-08-01", 4.33))

    def test_error_response_raises_datafeed_error(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_ERROR_RESPONSE)):
            with self.assertRaises(DataFeedError):
                fred.get_series("NOTREAL", "fake-key")

    def test_get_macro_snapshot_returns_one_entry_per_series_with_latest_and_previous(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_OBSERVATIONS_RESPONSE)):
            snapshot = fred.get_macro_snapshot("fake-key")
        self.assertEqual(len(snapshot), len(fred.SERIES))
        row = next(s for s in snapshot if s.key == "fed_funds_rate")
        self.assertEqual(row.latest_value, 4.33)
        self.assertEqual(row.previous_value, 4.58)

    def test_get_macro_snapshot_skips_a_series_that_errors_instead_of_failing_entirely(self):
        def flaky_urlopen(request, timeout=10):
            if "DFF" in request.full_url:
                raise __import__("urllib.error", fromlist=["URLError"]).URLError("boom")
            return _FakeResponse(SAMPLE_OBSERVATIONS_RESPONSE)

        with patch("urllib.request.urlopen", side_effect=flaky_urlopen):
            snapshot = fred.get_macro_snapshot("fake-key")
        self.assertEqual(len(snapshot), len(fred.SERIES) - 1)
        self.assertNotIn("fed_funds_rate", [s.key for s in snapshot])


class TestFredApiKeyResolution(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config_patch = patch.object(provider, "CONFIG_PATH", Path(self._tmpdir.name) / "config.json")
        self._config_patch.start()
        self._env_patch = patch.dict("os.environ", {}, clear=False)
        self._env_patch.start()
        import os

        os.environ.pop("FRED_API_KEY", None)

    def tearDown(self):
        self._config_patch.stop()
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_no_key_configured_returns_none(self):
        self.assertIsNone(fred.resolve_api_key())

    def test_env_var_takes_priority(self):
        provider.CONFIG_PATH.write_text(json.dumps({"fred_api_key": "from-config"}))
        with patch.dict("os.environ", {"FRED_API_KEY": "from-env"}):
            self.assertEqual(fred.resolve_api_key(), "from-env")

    def test_falls_back_to_config_file(self):
        provider.CONFIG_PATH.write_text(json.dumps({"fred_api_key": "from-config"}))
        self.assertEqual(fred.resolve_api_key(), "from-config")


if __name__ == "__main__":
    unittest.main()
