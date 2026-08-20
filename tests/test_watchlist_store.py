import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import watchlist_store
from aurum.datafeed.universe import DEFAULT_WATCHLIST


class TestWatchlistStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._path_patch = patch.object(watchlist_store, "WATCHLIST_PATH", Path(self._tmpdir.name) / "watchlist.json")
        self._path_patch.start()

    def tearDown(self):
        self._path_patch.stop()
        self._tmpdir.cleanup()

    def test_load_falls_back_to_default_when_no_file_exists(self):
        self.assertEqual(watchlist_store.load_watchlist(), list(DEFAULT_WATCHLIST))

    def test_load_falls_back_to_default_on_corrupt_file(self):
        watchlist_store.WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        watchlist_store.WATCHLIST_PATH.write_text("not json")
        self.assertEqual(watchlist_store.load_watchlist(), list(DEFAULT_WATCHLIST))

    def test_load_falls_back_to_default_on_empty_saved_list(self):
        # an intentionally-emptied watchlist still falls back to the default
        # rather than leaving the app with literally nothing to show
        watchlist_store.save_watchlist([])
        self.assertEqual(watchlist_store.load_watchlist(), list(DEFAULT_WATCHLIST))

    def test_add_symbol_appends_and_persists(self):
        result = watchlist_store.add_symbol("aapl")
        self.assertEqual(result, list(DEFAULT_WATCHLIST) + ["AAPL"])
        self.assertEqual(watchlist_store.load_watchlist(), list(DEFAULT_WATCHLIST) + ["AAPL"])

    def test_add_symbol_rejects_duplicate_case_insensitive(self):
        watchlist_store.add_symbol("AAPL")
        with self.assertRaises(watchlist_store.DuplicateSymbolError):
            watchlist_store.add_symbol("aapl")

    def test_add_symbol_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            watchlist_store.add_symbol("   ")

    def test_remove_symbol_removes_and_persists(self):
        result = watchlist_store.remove_symbol("SILVER")
        self.assertNotIn("SILVER", result)
        self.assertNotIn("SILVER", watchlist_store.load_watchlist())

    def test_remove_symbol_raises_when_not_present(self):
        with self.assertRaises(watchlist_store.SymbolNotFoundError):
            watchlist_store.remove_symbol("NOTREAL")

    def test_rename_symbol_replaces_in_place_preserving_order(self):
        result = watchlist_store.rename_symbol("SILVER", "PLATINUM")
        expected = [s if s != "SILVER" else "PLATINUM" for s in DEFAULT_WATCHLIST]
        self.assertEqual(result, expected)

    def test_rename_symbol_raises_when_old_name_not_present(self):
        with self.assertRaises(watchlist_store.SymbolNotFoundError):
            watchlist_store.rename_symbol("NOTREAL", "AAPL")

    def test_rename_symbol_raises_on_collision_with_existing_entry(self):
        with self.assertRaises(watchlist_store.DuplicateSymbolError):
            watchlist_store.rename_symbol("SILVER", "gold")  # case-insensitive collision with GOLD

    def test_rename_symbol_to_itself_is_a_no_op_not_an_error(self):
        result = watchlist_store.rename_symbol("GOLD", "gold")
        self.assertEqual(result, list(DEFAULT_WATCHLIST))


if __name__ == "__main__":
    unittest.main()
