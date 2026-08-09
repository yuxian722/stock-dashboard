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

    def test_header_rendered_as_sortable_submit_buttons(self):
        # 實機抓到的真實結構：這頁的表頭欄位是ASP.NET GridView做成的可排序
        # 按鈕，欄位名稱放在<input type="submit" value="ENTITY">的value屬性
        # 裡，不是<th>的文字內容——get_text()對<input>永遠抓到空字串，
        # 這是造成"共擷取到0筆"的真正原因(表頭列的candidate全是空字串，
        # 永遠比對不到ENTITY/STATUS，header判斷成None)。
        html = """
        <table>
          <tr>
            <th><input type="submit" value="ENTITY" name="ctl00$gvData$btnEntity"></th>
            <th><input type="submit" value="STATUS" name="ctl00$gvData$btnStatus"></th>
          </tr>
          <tr><td>BA721</td><td>IN-REPAIR</td></tr>
          <tr><td>BA231</td><td>SETUP</td></tr>
        </table>
        """
        records = pm.parse_pm_monitor_html(html)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0], {"ENTITY": "BA721", "STATUS": "IN-REPAIR"})
        self.assertEqual(records[1], {"ENTITY": "BA231", "STATUS": "SETUP"})

    def test_finds_data_rows_in_a_separate_table_from_the_header(self):
        # 實測抓到的真實情況：Syncfusion Grid把表頭跟資料內容拆成兩個不同的
        # <table>(方便凍結表頭)，表頭那個<table>裡完全沒有資料列。
        # 這是造成之前"共擷取到0筆"的真正原因，這裡鎖定要能跨table抓到資料。
        html = """
        <div class="e-gridheader">
          <table><tr><th>ENTITY</th><th>STATUS</th></tr></table>
        </div>
        <div class="e-gridcontent">
          <table>
            <tr><td>BA721</td><td>IN-REPAIR</td></tr>
            <tr><td>BA231</td><td>SETUP</td></tr>
          </table>
        </div>
        """
        records = pm.parse_pm_monitor_html(html)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["ENTITY"], "BA721")
        self.assertEqual(records[1]["ENTITY"], "BA231")

    def test_repeated_frozen_header_row_not_counted_as_data(self):
        # 凍結表頭機制有時會讓表頭列在畫面上重複出現(例如捲動用的複製列)，
        # 逐字相同的表頭列不該被當成一筆資料
        html = """
        <table>
          <tr><th>ENTITY</th><th>STATUS</th></tr>
          <tr><td>ENTITY</td><td>STATUS</td></tr>
          <tr><td>BA721</td><td>IN-REPAIR</td></tr>
        </table>
        """
        records = pm.parse_pm_monitor_html(html)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["ENTITY"], "BA721")


if __name__ == "__main__":
    unittest.main()
