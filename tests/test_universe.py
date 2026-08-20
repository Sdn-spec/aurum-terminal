import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.datafeed import universe


class TestIsCommodityOrIndexAlias(unittest.TestCase):
    def test_true_for_every_built_in_alias(self):
        for name in universe.ALIASES:
            self.assertTrue(universe.is_commodity_or_index_alias(name), name)

    def test_case_insensitive(self):
        self.assertTrue(universe.is_commodity_or_index_alias("gold"))
        self.assertTrue(universe.is_commodity_or_index_alias("Gold"))

    def test_false_for_a_real_stock_ticker(self):
        self.assertFalse(universe.is_commodity_or_index_alias("AAPL"))
        self.assertFalse(universe.is_commodity_or_index_alias("TSLA"))


if __name__ == "__main__":
    unittest.main()
