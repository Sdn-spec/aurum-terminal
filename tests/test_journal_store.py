import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.journal import store


class TestJournalStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._path_patch = patch.object(store, "JOURNAL_PATH", Path(self._tmpdir.name) / "journal.json")
        self._path_patch.start()

    def tearDown(self):
        self._path_patch.stop()
        self._tmpdir.cleanup()

    def test_load_journal_defaults_when_no_file_exists(self):
        journal = store.load_journal()
        self.assertEqual(journal["starting_equity"], store.DEFAULT_STARTING_EQUITY)
        self.assertEqual(journal["trades"], [])

    def test_load_journal_falls_back_to_default_on_corrupt_file(self):
        store.JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        store.JOURNAL_PATH.write_text("not json")
        journal = store.load_journal()
        self.assertEqual(journal["trades"], [])

    def test_add_trade_persists_and_returns_it(self):
        trade = store.add_trade(
            instrument="gold", direction="long", pnl=164.89, ts="2026-08-17T09:53:22",
            entry=4394.0, exit=4405.89, size=10, setup="support", notes="tapped swing low", confidence=4,
        )
        self.assertEqual(trade.instrument, "gold")
        self.assertEqual(trade.pnl, 164.89)
        self.assertTrue(trade.id)
        journal = store.load_journal()
        self.assertEqual(len(journal["trades"]), 1)
        self.assertEqual(journal["trades"][0].id, trade.id)
        self.assertEqual(journal["trades"][0].setup, "support")

    def test_add_trade_defaults_setup_to_none_and_notes_to_empty(self):
        trade = store.add_trade(instrument="GOLD", direction="short", pnl=-5.0, ts="2026-08-17T09:53:22")
        self.assertEqual(trade.setup, "none")
        self.assertEqual(trade.notes, "")

    def test_add_trade_rejects_empty_instrument(self):
        with self.assertRaises(ValueError):
            store.add_trade(instrument="  ", direction="long", pnl=1.0, ts="2026-08-17T09:53:22")

    def test_add_trade_rejects_bad_direction(self):
        with self.assertRaises(ValueError):
            store.add_trade(instrument="GOLD", direction="sideways", pnl=1.0, ts="2026-08-17T09:53:22")

    def test_add_trade_rejects_missing_pnl(self):
        with self.assertRaises(ValueError):
            store.add_trade(instrument="GOLD", direction="long", pnl=None, ts="2026-08-17T09:53:22")

    def test_add_trade_rejects_missing_ts(self):
        with self.assertRaises(ValueError):
            store.add_trade(instrument="GOLD", direction="long", pnl=1.0, ts="")

    def test_remove_trade_deletes_it(self):
        trade = store.add_trade(instrument="GOLD", direction="long", pnl=1.0, ts="2026-08-17T09:53:22")
        store.remove_trade(trade.id)
        self.assertEqual(store.load_journal()["trades"], [])

    def test_remove_trade_raises_when_not_found(self):
        with self.assertRaises(store.TradeNotFoundError):
            store.remove_trade("missing")


if __name__ == "__main__":
    unittest.main()
