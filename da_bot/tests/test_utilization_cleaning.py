"""cpis_utilization_scraper.py 資料清洗邏輯的離線單元測試（不連網）。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

import cpis_utilization_scraper as uscraper


class TestSplitHeader(unittest.TestCase):
    def test_basic(self):
        rows = [["Date", "Qty"], ["2026-08-01", "10"]]
        header, data = uscraper._split_header(rows)
        self.assertEqual(header, ["Date", "Qty"])
        self.assertEqual(data, [["2026-08-01", "10"]])

    def test_empty(self):
        self.assertEqual(uscraper._split_header([]), ([], []))


class TestNormalizeHeader(unittest.TestCase):
    def test_strips_fullwidth_space(self):
        self.assertEqual(uscraper._normalize_header("　Date　"), "Date")

    def test_collapses_whitespace(self):
        self.assertEqual(uscraper._normalize_header("Date   Time"), "Date Time")


class TestIsSummaryRow(unittest.TestCase):
    def test_true_for_sum(self):
        self.assertTrue(uscraper._is_summary_row(["SUM", "100"]))

    def test_true_for_chinese_keyword(self):
        self.assertTrue(uscraper._is_summary_row(["合計", "100"]))

    def test_false_for_normal_row(self):
        self.assertFalse(uscraper._is_summary_row(["2026-08-01", "Day", "10"]))

    def test_skips_leading_blank_cells(self):
        self.assertTrue(uscraper._is_summary_row(["", "TARGET", "5"]))


class TestApplyWhitelist(unittest.TestCase):
    def setUp(self):
        self._orig = uscraper.WHITELIST_COLUMNS

    def tearDown(self):
        uscraper.WHITELIST_COLUMNS = self._orig

    def test_noop_when_unset(self):
        uscraper.WHITELIST_COLUMNS = None
        header = ["Date", "Extra"]
        rows = [["2026-08-01", "x"]]
        new_header, new_rows = uscraper._apply_whitelist(header, rows)
        self.assertEqual(new_header, header)
        self.assertEqual(new_rows, rows)

    def test_filters_columns(self):
        uscraper.WHITELIST_COLUMNS = ("Date",)
        header = ["Date", "Extra"]
        rows = [["2026-08-01", "x"]]
        new_header, new_rows = uscraper._apply_whitelist(header, rows)
        self.assertEqual(new_header, ["Date"])
        self.assertEqual(new_rows, [["2026-08-01"]])

    def test_falls_back_when_nothing_matches(self):
        uscraper.WHITELIST_COLUMNS = ("NotPresent",)
        header = ["Date", "Extra"]
        rows = [["2026-08-01", "x"]]
        new_header, new_rows = uscraper._apply_whitelist(header, rows)
        self.assertEqual(new_header, header)
        self.assertEqual(new_rows, rows)


if __name__ == "__main__":
    unittest.main()
