import tempfile
import unittest
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond import screener_db
from bond.stock_screener import execute_strategy


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.stocks = [
            {"code": "000001", "name": "甲", "pe": 8.0, "pb": 1.2,
             "market_cap": 500.0, "net_assets": 200.0, "industry": "银行"},
            {"code": "000002", "name": "乙", "pe": 12.0, "pb": 0.9,
             "market_cap": 300.0, "net_assets": 150.0, "industry": "银行"},
            {"code": "000003", "name": "丙", "pe": 6.0, "pb": 1.8,
             "market_cap": 100.0, "net_assets": 50.0, "industry": "制造"},
        ]

    def test_range_then_sort_limit(self):
        result = execute_strategy(self.stocks, [
            {"type": "range", "field": "net_assets", "op": ">=", "value": 100},
            {"type": "sort_limit", "field": "pe", "dir": "asc", "limit": 1},
        ])
        self.assertEqual(["000001"], [row["code"] for row in result["stocks"]])
        self.assertEqual([3, 2, 1], [row["count"] for row in result["stats"]])

    def test_industry_cap_uses_cached_industries(self):
        result = execute_strategy(self.stocks, [
            {"type": "sort_limit", "field": "pe", "dir": "asc", "limit": 0},
            {"type": "industry_cap", "max": 1},
        ])
        self.assertEqual(["000003", "000001"], [row["code"] for row in result["stocks"]])

    def test_industry_filter(self):
        result = execute_strategy(self.stocks, [
            {"type": "industry_filter", "industries": ["银行"], "mode": "include"},
        ])
        self.assertEqual(["000001", "000002"], [row["code"] for row in result["stocks"]])


class ScreenerDbTests(unittest.TestCase):
    def test_get_industries_returns_distinct_sorted_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            screener_db.init_screener_db(tmpdir)
            screener_db.save_stocks("a_share", [
                {"code": "1", "name": "甲", "industry": "制造"},
                {"code": "2", "name": "乙", "industry": "银行"},
                {"code": "3", "name": "丙", "industry": "制造"},
                {"code": "4", "name": "丁", "industry": ""},
            ])
            self.assertEqual(["制造", "银行"], screener_db.get_industries("a_share"))

    def test_save_stocks_rolls_back_when_new_snapshot_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            screener_db.init_screener_db(tmpdir)
            screener_db.save_stocks("a_share", [
                {"code": "1", "name": "原数据", "industry": "银行"},
            ])

            with self.assertRaises(sqlite3.IntegrityError):
                screener_db.save_stocks("a_share", [
                    {"code": "2", "name": "重复一"},
                    {"code": "2", "name": "重复二"},
                ])

            rows = screener_db.get_all_stocks("a_share")
            self.assertEqual(["1"], [row["code"] for row in rows])


if __name__ == "__main__":
    unittest.main()
