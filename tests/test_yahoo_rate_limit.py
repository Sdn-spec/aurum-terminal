import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import yahoo


def _http_error(code=429):
    return urllib.error.HTTPError("http://x", code, "Too Many Requests", {}, None)


class TestRateLimitBreaker(unittest.TestCase):
    def setUp(self):
        yahoo.reset_rate_limit()

    def tearDown(self):
        yahoo.reset_rate_limit()

    def test_breaker_is_closed_by_default(self):
        self.assertFalse(yahoo.rate_limit_status()["open"])

    def test_a_single_429_does_not_trip_the_breaker(self):
        yahoo._note_rate_limited()
        self.assertFalse(yahoo.rate_limit_status()["open"])

    def test_consecutive_429s_trip_the_breaker(self):
        for _ in range(yahoo._RATE_LIMIT_TRIP_AFTER):
            yahoo._note_rate_limited()
        status = yahoo.rate_limit_status()
        self.assertTrue(status["open"])
        self.assertGreater(status["seconds_remaining"], 0)

    def test_a_success_clears_the_streak(self):
        yahoo._note_rate_limited()
        yahoo._note_rate_limited()
        yahoo._note_request_succeeded()
        yahoo._note_rate_limited()
        self.assertFalse(yahoo.rate_limit_status()["open"])

    def test_open_breaker_makes_fetch_fail_without_touching_the_network(self):
        for _ in range(yahoo._RATE_LIMIT_TRIP_AFTER):
            yahoo._note_rate_limited()
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(yahoo.RateLimitedError):
                yahoo.get_quote("^GSPC")
        urlopen.assert_not_called()

    def test_rate_limited_error_is_a_datafeed_error(self):
        """Every caller already handles DataFeedError; the breaker must not
        introduce an exception type that slips past those handlers."""
        self.assertTrue(issubclass(yahoo.RateLimitedError, yahoo.DataFeedError))

    def test_repeated_429s_stop_retrying_once_the_breaker_opens(self):
        """The retry loop is what turns one throttled caller into four
        requests -- once the breaker trips it must stop immediately."""
        with patch("urllib.request.urlopen", side_effect=_http_error(429)) as urlopen, \
             patch("time.sleep"):
            with self.assertRaises(yahoo.DataFeedError):
                yahoo.get_quote("^GSPC")
            calls_first = urlopen.call_count
            # breaker is open now: the next call must not reach the network
            with self.assertRaises(yahoo.RateLimitedError):
                yahoo.get_quote("^DJI")
            self.assertEqual(urlopen.call_count, calls_first)

    def test_non_429_errors_do_not_trip_the_breaker(self):
        with patch("urllib.request.urlopen", side_effect=_http_error(404)):
            with self.assertRaises(yahoo.DataFeedError):
                yahoo.get_quote("NOPE")
        self.assertFalse(yahoo.rate_limit_status()["open"])

    def test_reset_clears_an_open_breaker(self):
        for _ in range(yahoo._RATE_LIMIT_TRIP_AFTER):
            yahoo._note_rate_limited()
        self.assertTrue(yahoo.rate_limit_status()["open"])
        yahoo.reset_rate_limit()
        self.assertFalse(yahoo.rate_limit_status()["open"])


if __name__ == "__main__":
    unittest.main()
