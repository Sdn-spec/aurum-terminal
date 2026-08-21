import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.broker import ibkr
from aurum.datafeed import provider

# IBKR sends DBL_MAX where a number does not apply -- most visibly as
# realizedPNL on an opening fill.
DBL_MAX = 1.7976931348623157e308


class TestSettings(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config_patch = patch.object(provider, "CONFIG_PATH", Path(self._tmpdir.name) / "config.json")
        self._config_patch.start()
        self._env_patch = patch.dict("os.environ", {}, clear=False)
        self._env_patch.start()
        for var in ("IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID"):
            __import__("os").environ.pop(var, None)

    def tearDown(self):
        self._config_patch.stop()
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_defaults_to_the_gateway_paper_port(self):
        """Defaulting to a live trading account is not something this should
        do quietly."""
        s = ibkr.resolve_settings()
        self.assertEqual(s["host"], "127.0.0.1")
        self.assertEqual(s["port"], 4002)

    def test_config_file_overrides_defaults(self):
        provider.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        provider.CONFIG_PATH.write_text('{"ibkr_host": "10.0.0.5", "ibkr_port": 7497, "ibkr_client_id": 3}')
        s = ibkr.resolve_settings()
        self.assertEqual(s, {"host": "10.0.0.5", "port": 7497, "client_id": 3})

    def test_env_wins_over_config_file(self):
        import os
        provider.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        provider.CONFIG_PATH.write_text('{"ibkr_port": 7497}')
        os.environ["IBKR_PORT"] = "4001"
        self.assertEqual(ibkr.resolve_settings()["port"], 4001)

    def test_nonsense_port_falls_back_rather_than_crashing(self):
        provider.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        provider.CONFIG_PATH.write_text('{"ibkr_port": "not-a-port"}')
        self.assertEqual(ibkr.resolve_settings()["port"], ibkr.DEFAULT_PORT)


class TestUnsetSentinel(unittest.TestCase):
    def test_dbl_max_becomes_none(self):
        self.assertIsNone(ibkr._clean_float(DBL_MAX))
        self.assertIsNone(ibkr._clean_float(-DBL_MAX))

    def test_nan_becomes_none(self):
        self.assertIsNone(ibkr._clean_float(float("nan")))

    def test_real_numbers_survive(self):
        self.assertEqual(ibkr._clean_float(12.5), 12.5)
        self.assertEqual(ibkr._clean_float(0), 0.0)
        self.assertEqual(ibkr._clean_float("3.5"), 3.5)

    def test_none_and_junk_become_none(self):
        self.assertIsNone(ibkr._clean_float(None))
        self.assertIsNone(ibkr._clean_float("abc"))


def _fill(side="SLD", pnl=120.0, commission=1.5, price=4400.0, shares=2.0, exec_id="0001.abc"):
    return {
        "exec_id": exec_id, "time": "2026-08-21T10:15:00+00:00", "symbol": "GC",
        "sec_type": "FUT", "currency": "USD", "side": side, "shares": shares,
        "price": price, "commission": commission, "realized_pnl": pnl,
    }


class TestFillsToJournalTrades(unittest.TestCase):
    def test_opening_fills_without_realised_pnl_are_skipped(self):
        """An opening fill has no outcome yet; importing it would add a
        zero-P&L row that drags the win rate toward meaningless."""
        trades = ibkr.fills_to_journal_trades([_fill(pnl=None)])
        self.assertEqual(trades, [])

    def test_realised_fill_becomes_a_journal_trade(self):
        trades = ibkr.fills_to_journal_trades([_fill()])
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["instrument"], "GC")
        self.assertEqual(t["size"], 2.0)
        self.assertEqual(t["exit"], 4400.0)
        self.assertEqual(t["ts"], "2026-08-21T10:15:00+00:00")

    def test_pnl_is_net_of_commission(self):
        trades = ibkr.fills_to_journal_trades([_fill(pnl=120.0, commission=1.5)])
        self.assertAlmostEqual(trades[0]["pnl"], 118.5)

    def test_a_sell_that_realises_pnl_is_recorded_as_a_long(self):
        """The fill side is the side of the *closing* order, so a sell that
        realises P&L closed a long position."""
        self.assertEqual(ibkr.fills_to_journal_trades([_fill(side="SLD")])[0]["direction"], "long")
        self.assertEqual(ibkr.fills_to_journal_trades([_fill(side="BOT")])[0]["direction"], "short")

    def test_exec_id_is_recorded_so_imports_can_be_deduplicated(self):
        trades = ibkr.fills_to_journal_trades([_fill(exec_id="0001.deadbeef")])
        self.assertIn("0001.deadbeef", trades[0]["notes"])

    def test_produces_a_payload_the_journal_store_accepts(self):
        """Guards the seam: these dicts are handed straight to
        journal_store.add_trade, so its validation must pass."""
        from aurum.journal import store as journal_store
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(journal_store, "JOURNAL_PATH", Path(tmp) / "journal.json"):
                for trade in ibkr.fills_to_journal_trades([_fill()]):
                    journal_store.add_trade(**trade)
                self.assertEqual(len(journal_store.load_journal()["trades"]), 1)


class TestFailureHandling(unittest.TestCase):
    def test_status_reports_unavailable_instead_of_raising(self):
        """The page asks this on load; an unreachable gateway is the normal
        case, not an error."""
        with patch.object(ibkr, "_run_with_ib", side_effect=ibkr.BrokerError("no gateway")):
            status = ibkr.get_status()
        self.assertFalse(status["available"])
        self.assertIn("no gateway", status["detail"])
        self.assertIn("port", status)

    def test_status_reports_available_with_accounts(self):
        with patch.object(ibkr, "_run_with_ib", return_value=["DU123456"]):
            status = ibkr.get_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["accounts"], ["DU123456"])

    def test_broker_error_is_a_datafeed_error(self):
        from aurum.datafeed.yahoo import DataFeedError
        self.assertTrue(issubclass(ibkr.BrokerError, DataFeedError))

    def test_missing_library_is_reported_as_an_actionable_error(self):
        with patch.dict(sys.modules, {"ib_async": None}):
            with self.assertRaises(ibkr.BrokerError) as ctx:
                ibkr._run_with_ib(lambda ib: None, timeout=10)
        self.assertIn("ib_async", str(ctx.exception))

    def test_a_hung_gateway_times_out_rather_than_blocking_forever(self):
        import time

        def never_returns(ib):
            time.sleep(30)

        with patch("ib_async.IB") as FakeIB:
            FakeIB.return_value.isConnected.return_value = False
            t0 = time.time()
            with self.assertRaises(ibkr.BrokerError) as ctx:
                ibkr._run_with_ib(never_returns, timeout=1.5)
        self.assertLess(time.time() - t0, 8)
        self.assertIn("did not respond", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
