"""cpis_pm_monitor_scraper.py 的離線單元測試(不連網、不開瀏覽器)：只測純HTML
解析邏輯(parse_pm_monitor_html)，不測fetch_pm_monitor_html/records(要真的
開Selenium連CPIS，不適合離線單元測試，只能實機手動跑python cpis_pm_monitor_
scraper.py驗證)。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

import cpis_pm_monitor_scraper as pm


class TestParsePmMonitorHtml(unittest.TestCase):
    def test_parses_rows_matching_header(self):
        html = """
        <table>
          <tr><th>OPER</th><th>ENTITY</th><th>MODEL</th><th>STATUS</th>
              <th>LOT NO</th><th>Bond ID</th><th>WIP</th><th>IN TIME</th>
              <th>OUTPLAN</th><th>JCODE</th><th>OPERATOR</th></tr>
          <tr><td>DA</td><td>BA721</td><td>DIE-ATTACH</td><td>IN-REPAIR</td>
              <td>V32AWBF</td><td></td><td>52838</td><td>2026/08/09 16:34</td>
              <td></td><td>E</td><td>23535</td></tr>
          <tr><td>DA</td><td>BA231</td><td>DIE-ATTACH</td><td>SETUP</td>
              <td></td><td></td><td>0</td><td>2026/08/09 16:23</td>
              <td>2026/08/09 17:05</td><td>INK</td><td>28119</td></tr>
        </table>
        """
        records = pm.parse_pm_monitor_html(html)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["ENTITY"], "BA721")
        self.assertEqual(records[0]["STATUS"], "IN-REPAIR")
        self.assertEqual(records[1]["ENTITY"], "BA231")
        self.assertEqual(records[1]["STATUS"], "SETUP")

    def test_ignores_tables_without_entity_status_header(self):
        html = """
        <table>
          <tr><th>日期</th><th>ProductLine</th><th>Area</th></tr>
          <tr><td>2026/08/09</td><td>APG</td><td>DA</td></tr>
        </table>
        """
        records = pm.parse_pm_monitor_html(html)
        self.assertEqual(records, [])

    def test_skips_rows_with_mismatched_cell_count(self):
        html = """
        <table>
          <tr><th>ENTITY</th><th>STATUS</th></tr>
          <tr><td>BA721</td><td>IN-REPAIR</td></tr>
          <tr><td>只有一格</td></tr>
        </table>
        """
        records = pm.parse_pm_monitor_html(html)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["ENTITY"], "BA721")

    def test_no_table_at_all_returns_empty_list(self):
        self.assertEqual(pm.parse_pm_monitor_html("<html><body>沒有表格</body></html>"), [])

    def test_picks_first_matching_table_when_multiple_present(self):
        html = """
        <table><tr><th>日期</th></tr><tr><td>x</td></tr></table>
        <table>
          <tr><th>ENTITY</th><th>STATUS</th></tr>
          <tr><td>BA721</td><td>IN-REPAIR</td></tr>
        </table>
        """
        records = pm.parse_pm_monitor_html(html)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["ENTITY"], "BA721")


if __name__ == "__main__":
    unittest.main()
