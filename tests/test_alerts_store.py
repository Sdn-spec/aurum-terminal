import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.alerts import store


class TestAlertsStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._path_patch = patch.object(store, "ALERTS_PATH", Path(self._tmpdir.name) / "alerts.json")
        self._path_patch.start()

    def tearDown(self):
        self._path_patch.stop()
        self._tmpdir.cleanup()

    def test_load_price_alerts_is_empty_when_no_file_exists(self):
        self.assertEqual(store.load_price_alerts(), [])

    def test_load_price_alerts_falls_back_to_empty_on_corrupt_file(self):
        store.ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        store.ALERTS_PATH.write_text("not json")
        self.assertEqual(store.load_price_alerts(), [])

    def test_add_price_alert_persists_and_returns_it(self):
        alert = store.add_price_alert("gold", "above", 4500.0)
        self.assertEqual(alert.symbol, "GOLD")
        self.assertEqual(alert.condition, "above")
        self.assertEqual(alert.price, 4500.0)
        self.assertTrue(alert.id)
        loaded = store.load_price_alerts()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].id, alert.id)

    def test_add_price_alert_rejects_bad_condition(self):
        with self.assertRaises(ValueError):
            store.add_price_alert("GOLD", "sideways", 4500.0)

    def test_add_price_alert_rejects_non_positive_price(self):
        with self.assertRaises(ValueError):
            store.add_price_alert("GOLD", "above", 0)
        with self.assertRaises(ValueError):
            store.add_price_alert("GOLD", "above", -5)

    def test_add_price_alert_rejects_empty_symbol(self):
        with self.assertRaises(ValueError):
            store.add_price_alert("   ", "above", 100)

    def test_remove_price_alert_deletes_it(self):
        alert = store.add_price_alert("GOLD", "above", 4500.0)
        store.remove_price_alert(alert.id)
        self.assertEqual(store.load_price_alerts(), [])

    def test_remove_price_alert_raises_when_not_found(self):
        with self.assertRaises(store.AlertNotFoundError):
            store.remove_price_alert("nope")

    def test_multiple_alerts_coexist_independently(self):
        a = store.add_price_alert("GOLD", "above", 4500.0)
        b = store.add_price_alert("BTC", "below", 60000.0)
        loaded = {row.id: row for row in store.load_price_alerts()}
        self.assertEqual(loaded[a.id].symbol, "GOLD")
        self.assertEqual(loaded[b.id].symbol, "BTC")
        store.remove_price_alert(a.id)
        remaining = store.load_price_alerts()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].id, b.id)

    def test_last_verdict_round_trips(self):
        self.assertIsNone(store.get_last_verdict("GOLD"))
        store.set_last_verdict("gold", "INVEST")
        self.assertEqual(store.get_last_verdict("GOLD"), "INVEST")
        self.assertEqual(store.get_last_verdict("gold"), "INVEST")  # case-insensitive lookup

    def test_last_verdict_overwrites_on_change(self):
        store.set_last_verdict("GOLD", "WATCH")
        store.set_last_verdict("GOLD", "INVEST")
        self.assertEqual(store.get_last_verdict("GOLD"), "INVEST")

    def test_last_verdicts_and_price_alerts_are_independent(self):
        store.add_price_alert("GOLD", "above", 4500.0)
        store.set_last_verdict("GOLD", "INVEST")
        self.assertEqual(len(store.load_price_alerts()), 1)
        self.assertEqual(store.get_last_verdict("GOLD"), "INVEST")


if __name__ == "__main__":
    unittest.main()
