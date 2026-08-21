import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import twelvedata


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http429():
    return urllib.error.HTTPError("url", 429, "Too Many Requests", {}, io.BytesIO())


OUT_OF_CREDITS = {
    "code": 429,
    "status": "error",
    "message": "You have run out of API credits for the day. 11001 API credits were used, "
               "with the current limit being 800.",
}
PLAN_RESTRICTED = {
    "code": 403,
    "status": "error",
    "message": "SPX is available starting with the Grow or Venture plan.",
}


class TestDailyQuotaGuard(unittest.TestCase):
    def setUp(self):
        twelvedata.reset_quota_guard()
        twelvedata.reset_plan_memo()

    def tearDown(self):
        twelvedata.reset_quota_guard()
        twelvedata.reset_plan_memo()

    def test_quota_guard_starts_closed(self):
        self.assertFalse(twelvedata.quota_status()["exhausted"])

    def test_http_429_trips_the_guard(self):
        with patch("urllib.request.urlopen", side_effect=_http429()):
            with self.assertRaises(twelvedata.QuotaExhaustedError):
                twelvedata.get_quote("XAU/USD", "key")
        self.assertTrue(twelvedata.quota_status()["exhausted"])

    def test_error_body_out_of_credits_trips_the_guard(self):
        """The exhaustion can arrive as a 200 with an error body, not just a 429."""
        with patch("urllib.request.urlopen", return_value=_Resp(OUT_OF_CREDITS)):
            with self.assertRaises(twelvedata.QuotaExhaustedError):
                twelvedata.get_quote("XAU/USD", "key")
        self.assertTrue(twelvedata.quota_status()["exhausted"])

    def test_once_tripped_no_further_network_calls_are_made(self):
        """This is the whole point: 11,001 credits were burned against an 800
        limit by continuing to ask after the answer was already known."""
        with patch("urllib.request.urlopen", side_effect=_http429()) as urlopen:
            with self.assertRaises(twelvedata.QuotaExhaustedError):
                twelvedata.get_quote("XAU/USD", "key")
            calls = urlopen.call_count
            for _ in range(5):
                with self.assertRaises(twelvedata.QuotaExhaustedError):
                    twelvedata.get_quote("BTC/USD", "key")
            self.assertEqual(urlopen.call_count, calls)

    def test_guard_resets_within_a_day(self):
        with patch("urllib.request.urlopen", side_effect=_http429()):
            with self.assertRaises(twelvedata.QuotaExhaustedError):
                twelvedata.get_quote("XAU/USD", "key")
        secs = twelvedata.quota_status()["seconds_until_reset"]
        self.assertGreater(secs, 0)
        self.assertLessEqual(secs, 24 * 3600 + 1)

    def test_quota_error_is_a_datafeed_error(self):
        self.assertTrue(issubclass(twelvedata.QuotaExhaustedError, twelvedata.DataFeedError))


class TestPlanRestrictionMemo(unittest.TestCase):
    def setUp(self):
        twelvedata.reset_quota_guard()
        twelvedata.reset_plan_memo()

    def tearDown(self):
        twelvedata.reset_quota_guard()
        twelvedata.reset_plan_memo()

    def test_plan_restriction_raises_a_distinct_error(self):
        with patch("urllib.request.urlopen", return_value=_Resp(PLAN_RESTRICTED)):
            with self.assertRaises(twelvedata.SymbolNotOnPlanError):
                twelvedata.get_quote("SPX", "key")

    def test_a_restricted_symbol_is_not_requested_again(self):
        """Each refusal still costs a credit, so asking every poll spends the
        day's allowance on an answer that cannot change."""
        with patch("urllib.request.urlopen", return_value=_Resp(PLAN_RESTRICTED)) as urlopen:
            with self.assertRaises(twelvedata.SymbolNotOnPlanError):
                twelvedata.get_quote("SPX", "key")
            self.assertEqual(urlopen.call_count, 1)
            for _ in range(4):
                with self.assertRaises(twelvedata.SymbolNotOnPlanError):
                    twelvedata.get_quote("SPX", "key")
            self.assertEqual(urlopen.call_count, 1)  # still just the one

    def test_other_symbols_are_unaffected_by_one_restriction(self):
        with patch("urllib.request.urlopen", return_value=_Resp(PLAN_RESTRICTED)):
            with self.assertRaises(twelvedata.SymbolNotOnPlanError):
                twelvedata.get_quote("SPX", "key")
        ok = {"close": "4462.1", "high": "4470", "low": "4450", "currency": "USD",
              "exchange": "COMEX", "timestamp": 1700000000, "previous_close": "4400",
              "open": "4410", "volume": "1000"}
        with patch("urllib.request.urlopen", return_value=_Resp(ok)):
            quote = twelvedata.get_quote("XAU/USD", "key")
        self.assertAlmostEqual(quote.price, 4462.1)

    def test_plan_error_is_a_datafeed_error(self):
        self.assertTrue(issubclass(twelvedata.SymbolNotOnPlanError, twelvedata.DataFeedError))


if __name__ == "__main__":
    unittest.main()
