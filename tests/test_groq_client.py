import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import provider
from aurum.datafeed.yahoo import DataFeedError
from aurum.llm import groq_client

SAMPLE_REPORT = {
    "symbol": "AAPL",
    "last_close": 316.83,
    "verdict": "INVEST",
    "confidence": "High",
    "score": 4,
    "research": {
        "trend_regime": "Uptrend",
        "momentum_pct": 2.5,
        "volatility_annualized_pct": 22.1,
        "distance_from_year_high_pct": -8.1,
        "distance_from_year_low_pct": 41.5,
    },
    "debate": {"bull_points": ["Trend is up", "Momentum positive"], "bear_points": ["Volume unconfirmed"]},
    "day_trade": {"direction": "long", "entry": 316.83, "stop": 299.4, "take_profit_1": 343.0, "take_profit_2": 369.1, "risk_reward_ratio": 1.5},
    "long_term": {"direction": "long", "entry": 316.83, "stop": 268.5, "take_profit_1": 391.0, "take_profit_2": 461.8, "risk_reward_ratio": 1.5},
    "risk": {"status": "PROTECTED"},
    "fundamentals": {"pe_ttm": 34.4, "beta": 1.09, "dividend_yield_pct": 0.51},
    "earnings": {"date": "2026-10-28"},
    "macro": [{"label": "Fed funds rate", "latest_value": 3.63}],
}

SAMPLE_GROQ_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "AAPL looks constructive here..."}}],
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


class TestBuildPrompt(unittest.TestCase):
    def test_includes_the_key_facts(self):
        prompt = groq_client.build_prompt(SAMPLE_REPORT)
        self.assertIn("AAPL", prompt)
        self.assertIn("INVEST", prompt)
        self.assertIn("Trend is up", prompt)
        self.assertIn("Volume unconfirmed", prompt)
        self.assertIn("PROTECTED", prompt)
        self.assertIn("34.40", prompt)  # P/E from fundamentals
        self.assertIn("2026-10-28", prompt)  # earnings date
        self.assertIn("Fed funds rate", prompt)  # macro

    def test_handles_missing_optional_sections_gracefully(self):
        minimal = dict(SAMPLE_REPORT)
        minimal["fundamentals"] = None
        minimal["earnings"] = None
        minimal["macro"] = []
        prompt = groq_client.build_prompt(minimal)
        self.assertIn("AAPL", prompt)  # doesn't crash without fundamentals/earnings/macro


class TestGenerateNarrative(unittest.TestCase):
    def test_parses_the_documented_response_shape(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(SAMPLE_GROQ_RESPONSE)):
            text = groq_client.generate_narrative(SAMPLE_REPORT, "fake-key")
        self.assertEqual(text, "AAPL looks constructive here...")

    def test_unexpected_response_shape_raises_datafeed_error(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse({"unexpected": "shape"})):
            with self.assertRaises(DataFeedError):
                groq_client.generate_narrative(SAMPLE_REPORT, "fake-key")

    def test_bare_timeout_error_is_wrapped_as_datafeed_error(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(DataFeedError):
                groq_client.generate_narrative(SAMPLE_REPORT, "fake-key")


class TestApiKeyResolution(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config_patch = patch.object(provider, "CONFIG_PATH", Path(self._tmpdir.name) / "config.json")
        self._config_patch.start()
        self._env_patch = patch.dict("os.environ", {}, clear=False)
        self._env_patch.start()
        import os

        os.environ.pop("GROQ_API_KEY", None)

    def tearDown(self):
        self._config_patch.stop()
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_no_key_configured_returns_none(self):
        self.assertIsNone(groq_client.resolve_api_key())

    def test_env_var_takes_priority(self):
        provider.CONFIG_PATH.write_text(json.dumps({"groq_api_key": "from-config"}))
        with patch.dict("os.environ", {"GROQ_API_KEY": "from-env"}):
            self.assertEqual(groq_client.resolve_api_key(), "from-env")

    def test_falls_back_to_config_file(self):
        provider.CONFIG_PATH.write_text(json.dumps({"groq_api_key": "from-config"}))
        self.assertEqual(groq_client.resolve_api_key(), "from-config")


if __name__ == "__main__":
    unittest.main()
