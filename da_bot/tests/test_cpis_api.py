"""cpis_api.py 純邏輯（不連網）的離線單元測試：隱藏欄位解析、表格解析（含 rowspan
展開）、編碼自動偵測、驗證失敗判斷。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

import cpis_api

SIMPLE_TABLE_HTML = """
<html><body>
<table id="gvData">
<tr><th>Date</th><th>Shift</th><th>Qty</th></tr>
<tr><td>2026-08-01</td><td>Day</td><td>10</td></tr>
<tr><td>2026-08-02</td><td>Night</td><td>20</td></tr>
</table>
</body></html>
"""

ROWSPAN_TABLE_HTML = """
<html><body>
<table id="gvData">
<tr><th>Date</th><th>Shift</th><th>Qty</th></tr>
<tr><td rowspan="2">2026-08-01</td><td>Day</td><td>10</td></tr>
<tr><td>Night</td><td>20</td></tr>
</table>
</body></html>
"""


class TestExtractInput(unittest.TestCase):
    def test_found(self):
        html = '<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="abc123" />'
        self.assertEqual(cpis_api.extract_input(html, "__VIEWSTATE"), "abc123")

    def test_missing(self):
        self.assertEqual(cpis_api.extract_input("<div>no inputs here</div>", "__VIEWSTATE"), "")

    def test_empty_value(self):
        html = '<input type="hidden" name="__EVENTVALIDATION" value="" />'
        self.assertEqual(cpis_api.extract_input(html, "__EVENTVALIDATION"), "")


class TestIsAuthFail(unittest.TestCase):
    def test_by_url(self):
        self.assertTrue(cpis_api.is_auth_fail("<html></html>", "http://host/CPISWeb/Logon.aspx"))

    def test_by_body(self):
        self.assertTrue(cpis_api.is_auth_fail("系統停滯過久，請重新登入", "http://host/x.aspx"))

    def test_false(self):
        self.assertFalse(cpis_api.is_auth_fail("<html>正常資料頁</html>", "http://host/data.aspx"))


class TestParseTable(unittest.TestCase):
    def test_basic(self):
        rows = cpis_api.parse_table(SIMPLE_TABLE_HTML, "gvData")
        self.assertEqual(
            rows,
            [
                ["Date", "Shift", "Qty"],
                ["2026-08-01", "Day", "10"],
                ["2026-08-02", "Night", "20"],
            ],
        )

    def test_wrong_id_returns_empty(self):
        self.assertEqual(cpis_api.parse_table(SIMPLE_TABLE_HTML, "no_such_table"), [])

    def test_does_not_expand_rowspan(self):
        rows = cpis_api.parse_table(ROWSPAN_TABLE_HTML, "gvData")
        # 沒有展開 rowspan：第三列只有 2 個欄位，跟表頭欄位數對不齊
        self.assertEqual(rows[2], ["Night", "20"])

    def test_filled_expands_rowspan(self):
        rows = cpis_api.parse_table_filled(ROWSPAN_TABLE_HTML, "gvData")
        self.assertEqual(
            rows,
            [
                ["Date", "Shift", "Qty"],
                ["2026-08-01", "Day", "10"],
                ["2026-08-01", "Night", "20"],
            ],
        )


class TestDecodeBest(unittest.TestCase):
    def test_prefers_correct_utf8(self):
        raw = "測試中文字串".encode("utf-8")
        text, enc = cpis_api.decode_best(raw)
        self.assertEqual(text, "測試中文字串")
        self.assertEqual(enc, "utf-8")

    def test_prefers_correct_big5(self):
        raw = "測試中文字串".encode("big5")
        text, enc = cpis_api.decode_best(raw)
        self.assertEqual(text, "測試中文字串")
        self.assertIn(enc, ("big5", "cp950"))


class TestEeOperFields(unittest.TestCase):
    def test_matches_entity_checkbox(self):
        html = (
            '<input type="checkbox" name="DropDownCheckBoxes1$ctl02" value="0" />DA'
            '<input type="checkbox" name="DropDownCheckBoxes1$ctl03" value="1" />DB'
        )
        fields = cpis_api._ee_oper_fields(html, "DA")
        self.assertEqual(fields, {"DropDownCheckBoxes1$ctl02": "on"})


class TestIframeSrcs(unittest.TestCase):
    def test_extracts_all(self):
        html = '<iframe src="/a.aspx"></iframe><iframe src="/b.aspx"></iframe>'
        self.assertEqual(cpis_api._iframe_srcs(html), ["/a.aspx", "/b.aspx"])


if __name__ == "__main__":
    unittest.main()
