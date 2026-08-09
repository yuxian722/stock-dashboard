"""cpis_utilization_scraper.py 的離線單元測試(不連網)：這裡覆蓋08/06修過的
7個bug對應的邏輯(白名單制、rowspan補值、SUM/TARGET過濾、欄位別名/正規化)，
改版時原封不動保留，這裡確認沒有跑壞。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

from bs4 import BeautifulSoup

import cpis_utilization_scraper as uscraper


def _tables(html):
    return BeautifulSoup(html, "html.parser").find_all("table")


NON_DATA_TABLE_HTML = """
<table>
    <tr><th>Start Date</th><th>End Date</th><th>Fetch</th></tr>
    <tr><td>2026/08/01</td><td>2026/08/09</td><td>Go</td></tr>
</table>
"""

# 表頭用GROUP(需正規化成MODEL)。第二列少了MODEL欄(模擬rowspan合併儲存格被省略)，
# 第三列MODEL=SUM是小計列，第四列ENTITY=TARGET是目標列，兩者都該被過濾掉。
UTIL_DATA_TABLE_HTML = """
<table>
    <tr><th>GROUP</th><th>ENTITY</th><th>UTIL</th></tr>
    <tr><td>2100SD</td><td>BA205</td><td>95%</td></tr>
    <tr><td>BA206</td><td>90%</td></tr>
    <tr><td>SUM</td><td></td><td>92%</td></tr>
    <tr><td>2100SD</td><td>TARGET</td><td>100%</td></tr>
</table>
"""


class TestNormalizeHeaders(unittest.TestCase):
    def test_alias_group_to_model(self):
        headers = uscraper._normalize_headers(["GROUP", "ENTITY", "UTIL"])
        self.assertEqual(headers, ["MODEL", "ENTITY", "UTIL"])

    def test_blank_header_gets_positional_name(self):
        headers = uscraper._normalize_headers(["MODEL", ""])
        self.assertEqual(headers, ["MODEL", "col_1"])

    def test_duplicate_header_gets_suffix(self):
        headers = uscraper._normalize_headers(["UTIL", "UTIL"])
        self.assertEqual(headers, ["UTIL", "UTIL_1"])


class TestParseTables(unittest.TestCase):
    def test_non_data_table_skipped(self):
        records = uscraper.parse_tables(_tables(NON_DATA_TABLE_HTML))
        self.assertEqual(records, [])

    def test_data_table_parsed_with_rowspan_fill_and_summary_filter(self):
        records = uscraper.parse_tables(_tables(UTIL_DATA_TABLE_HTML))
        # SUM列(MODEL=SUM)跟TARGET列(ENTITY=TARGET)都該被濾掉，只剩前兩筆機台明細
        self.assertEqual(len(records), 2)

        self.assertEqual(records[0]["MODEL"], "2100SD")
        self.assertEqual(records[0]["ENTITY"], "BA205")
        self.assertEqual(records[0]["UTIL"], "95%")

        # 第二列少了MODEL欄(rowspan省略)，應該用前一列記住的MODEL值補上
        self.assertEqual(records[1]["MODEL"], "2100SD")
        self.assertEqual(records[1]["ENTITY"], "BA206")
        self.assertEqual(records[1]["UTIL"], "90%")

    def test_empty_tables_returns_empty(self):
        self.assertEqual(uscraper.parse_tables([]), [])

    def test_mixed_data_and_non_data_tables_only_data_kept(self):
        tables = _tables(NON_DATA_TABLE_HTML) + _tables(UTIL_DATA_TABLE_HTML)
        records = uscraper.parse_tables(tables)
        self.assertEqual(len(records), 2)


class TestToFloat(unittest.TestCase):
    def test_strips_percent_sign(self):
        self.assertEqual(uscraper._to_float("54.5%"), 54.5)

    def test_invalid_returns_none(self):
        self.assertIsNone(uscraper._to_float("n/a"))


if __name__ == "__main__":
    unittest.main()
