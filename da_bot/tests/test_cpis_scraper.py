"""cpis_scraper.py 的離線單元測試(不連網)：結果表格解析、日期時間拆分、
數字轉換。這些邏輯沿用自Selenium版，改版時沒有變動，這裡確認沒有跑壞。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

import cpis_scraper


RESULT_HTML = """
<html><body>
<table>
    <tr><td>filter noise</td></tr>
</table>
<table id="mainTable">
    <tr><th></th><th></th><th></th><th></th><th></th></tr>
    <tr>
        <td>B2</td><td>BA205</td>
        <td>2026/08/01 07:00</td><td>2026/08/01 07:05</td><td>2026/08/01 09:20</td>
    </tr>
    <tr>
        <td>B2</td><td>BA206</td>
        <td>2026/08/01 08:00</td><td>2026/08/01 08:10</td><td></td>
    </tr>
</table>
</body></html>
"""


class TestParseResultTable(unittest.TestCase):
    def test_picks_biggest_table_and_maps_col_n(self):
        records = cpis_scraper.parse_result_table(RESULT_HTML)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["col_0"], "B2")
        self.assertEqual(records[0]["col_1"], "BA205")
        self.assertEqual(records[0]["col_2"], "2026/08/01 07:00")

    def test_no_tables_returns_empty(self):
        self.assertEqual(cpis_scraper.parse_result_table("<html><body></body></html>"), [])

    def test_header_only_returns_empty(self):
        html = "<table><tr><th>A</th><th>B</th></tr></table>"
        self.assertEqual(cpis_scraper.parse_result_table(html), [])

    def test_mismatched_cell_count_row_skipped(self):
        html = """
        <table>
            <tr><th>A</th><th>B</th></tr>
            <tr><td>1</td><td>2</td></tr>
            <tr><td>only-one</td></tr>
        </table>
        """
        records = cpis_scraper.parse_result_table(html)
        self.assertEqual(len(records), 1)


class TestSplitDatetime(unittest.TestCase):
    def test_splits_date_and_time(self):
        self.assertEqual(cpis_scraper._split_datetime("2026/07/16 07:01"), ("2026-07-16", "07:01"))

    def test_empty_string_returns_none_none(self):
        self.assertEqual(cpis_scraper._split_datetime(""), (None, None))

    def test_none_returns_none_none(self):
        self.assertEqual(cpis_scraper._split_datetime(None), (None, None))

    def test_date_only_no_time(self):
        self.assertEqual(cpis_scraper._split_datetime("2026/07/16"), ("2026-07-16", None))


class TestToFloat(unittest.TestCase):
    def test_valid_number_string(self):
        self.assertEqual(cpis_scraper._to_float("1.5"), 1.5)

    def test_invalid_string_returns_none(self):
        self.assertIsNone(cpis_scraper._to_float("abc"))

    def test_none_returns_none(self):
        self.assertIsNone(cpis_scraper._to_float(None))


if __name__ == "__main__":
    unittest.main()
