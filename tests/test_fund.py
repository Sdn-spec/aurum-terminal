import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed.yahoo import HistoryBar
from aurum.fund.engine import scan_watchlist


def _bars(n=200, start=100.0, step=0.3, seed=1, volume=1000.0):
    rng = random.Random(seed)
    bars, price, ts = [], start, 1_700_000_000
    for i in range(n):
        price = price + step + rng.gauss(0, 0.05)
        o = price - 0.05
        bars.append(HistoryBar(ts, o, price + 0.1, price - 0.1, price, volume))
        ts += 86400
    return bars


class TestFundEngine(unittest.TestCase):
    def test_scans_every_symbol_in_the_watchlist(self):
        watchlist = {name: _bars(seed=i) for i, name in enumerate(["GOLD", "SILVER", "BTC"])}
        report = scan_watchlist(watchlist, equity=3000.0, peak_equity=3000.0)
        self.assertEqual({e.symbol for e in report.entries}, {"GOLD", "SILVER", "BTC"})
        for entry in report.entries:
            self.assertIsNone(entry.error)
            self.assertIsNotNone(entry.memo)

    def test_symbol_with_too_little_history_reports_error_not_crash(self):
        watchlist = {"GOLD": _bars(seed=0), "SILVER": _bars(n=5, seed=1)}
        report = scan_watchlist(watchlist, equity=3000.0, peak_equity=3000.0)
        silver = next(e for e in report.entries if e.symbol == "SILVER")
        self.assertIsNone(silver.memo)
        self.assertIsNotNone(silver.error)
        gold = next(e for e in report.entries if e.symbol == "GOLD")
        self.assertIsNotNone(gold.memo)

    def test_approved_and_watchlist_symbols_are_derived_from_verdicts(self):
        watchlist = {name: _bars(seed=i) for i, name in enumerate(["A", "B", "C"])}
        report = scan_watchlist(watchlist, equity=3000.0, peak_equity=3000.0)
        approved_from_entries = {e.symbol for e in report.entries if e.memo and e.memo.verdict == "APPROVED"}
        watchlisted_from_entries = {e.symbol for e in report.entries if e.memo and e.memo.verdict == "WATCHLIST"}
        self.assertEqual(set(report.approved_symbols), approved_from_entries)
        self.assertEqual(set(report.watchlist_symbols), watchlisted_from_entries)

    def test_allocation_present_when_enough_symbols_have_enough_data(self):
        watchlist = {name: _bars(n=200, seed=i) for i, name in enumerate(["A", "B", "C", "D"])}
        report = scan_watchlist(watchlist, equity=3000.0, peak_equity=3000.0)
        self.assertIsNotNone(report.allocation)
        self.assertAlmostEqual(sum(report.allocation.weights.values()), 1.0, places=4)

    def test_no_allocation_with_only_one_scannable_symbol(self):
        watchlist = {"GOLD": _bars(seed=0)}
        report = scan_watchlist(watchlist, equity=3000.0, peak_equity=3000.0)
        self.assertIsNone(report.allocation)

    def test_empty_watchlist_returns_empty_report(self):
        report = scan_watchlist({}, equity=3000.0, peak_equity=3000.0)
        self.assertEqual(report.entries, [])
        self.assertIsNone(report.allocation)
        self.assertEqual(report.approved_symbols, [])

    def test_optimizer_failure_degrades_to_no_allocation_instead_of_crashing(self):
        # skfolio can raise on a near-singular/degenerate correlation structure
        # (this is exactly what an unstable test-data seed surfaced during
        # development) — the fund scan as a whole must survive that, not 500.
        watchlist = {name: _bars(n=200, seed=i) for i, name in enumerate(["A", "B", "C"])}
        with patch("aurum.fund.engine.optimize", side_effect=ValueError("attempt to get argmax of an empty sequence")):
            report = scan_watchlist(watchlist, equity=3000.0, peak_equity=3000.0)
        self.assertIsNone(report.allocation)
        self.assertEqual(len(report.entries), 3)  # the per-symbol scans still completed fine


if __name__ == "__main__":
    unittest.main()
