"""cpis_api.py 純邏輯(不連網)的離線單元測試：隱藏欄位/表單欄位解析、編碼自動偵測、
驗證失敗判斷、Utilization資料URL組合、iframe來源擷取。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

import cpis_api


class TestExtractInput(unittest.TestCase):
    def test_found(self):
        html = '<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="abc123" />'
        self.assertEqual(cpis_api.extract_input(html, "__VIEWSTATE"), "abc123")

    def test_missing(self):
        self.assertEqual(cpis_api.extract_input("<div>no inputs here</div>", "__VIEWSTATE"), "")

    def test_empty_value(self):
        html = '<input type="hidden" name="__EVENTVALIDATION" value="" />'
        self.assertEqual(cpis_api.extract_input(html, "__EVENTVALIDATION"), "")


class TestExtractFormFields(unittest.TestCase):
    def test_text_and_hidden_inputs(self):
        html = """
        <form id="form1">
            <input type="hidden" name="__VIEWSTATE" value="vs123" />
            <input type="text" name="txtStart_date" value="20260101" />
        </form>
        """
        fields = cpis_api.extract_form_fields(html)
        self.assertEqual(fields["__VIEWSTATE"], "vs123")
        self.assertEqual(fields["txtStart_date"], "20260101")

    def test_checkbox_only_included_when_checked(self):
        html = """
        <form>
            <input type="checkbox" name="cbA" value="on" checked />
            <input type="checkbox" name="cbB" value="on" />
        </form>
        """
        fields = cpis_api.extract_form_fields(html)
        self.assertEqual(fields.get("cbA"), "on")
        self.assertNotIn("cbB", fields)

    def test_select_uses_selected_option(self):
        html = """
        <form>
            <select name="ddl_floor">
                <option value="None">None</option>
                <option value="A2" selected>A2</option>
            </select>
        </form>
        """
        fields = cpis_api.extract_form_fields(html)
        self.assertEqual(fields["ddl_floor"], "A2")

    def test_select_defaults_to_first_option_when_none_selected(self):
        html = """
        <form>
            <select name="ddl_shift">
                <option value="None">None</option>
                <option value="Day">Day</option>
            </select>
        </form>
        """
        fields = cpis_api.extract_form_fields(html)
        self.assertEqual(fields["ddl_shift"], "None")

    def test_submit_buttons_excluded(self):
        html = """
        <form>
            <input type="submit" name="btnFetch" value="Fetch" />
            <input type="text" name="txtEngineer" value="" />
        </form>
        """
        fields = cpis_api.extract_form_fields(html)
        self.assertNotIn("btnFetch", fields)
        self.assertIn("txtEngineer", fields)

    def test_no_form_returns_empty_dict(self):
        self.assertEqual(cpis_api.extract_form_fields("<div>no form</div>"), {})


class TestIsAuthFail(unittest.TestCase):
    def test_by_url(self):
        self.assertTrue(cpis_api.is_auth_fail("<html></html>", "http://host/CPISWeb/Logon.aspx"))

    def test_by_body(self):
        self.assertTrue(cpis_api.is_auth_fail("系統停滯過久，請重新登入", "http://host/x.aspx"))

    def test_false(self):
        self.assertFalse(cpis_api.is_auth_fail("<html>正常資料頁</html>", "http://host/data.aspx"))


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


class TestUtilizationDataUrl(unittest.TestCase):
    def test_contains_dates_and_operation(self):
        url = cpis_api._utilization_data_url("20260801", "20260809", "DA")
        self.assertIn("start_date=20260801", url)
        self.assertIn("end_date=20260809", url)
        self.assertIn("operation=DA", url)
        self.assertTrue(url.startswith(cpis_api.UTIL_BASE + cpis_api.UTIL_DATA_PATH))


class TestIframeSrcs(unittest.TestCase):
    def test_extracts_frame_and_iframe(self):
        html = '<frame src="/a.aspx"></frame><iframe src="/b.aspx"></iframe>'
        self.assertEqual(cpis_api._iframe_srcs(html), ["/a.aspx", "/b.aspx"])

    def test_no_frames_returns_empty(self):
        self.assertEqual(cpis_api._iframe_srcs("<div>no frames</div>"), [])


if __name__ == "__main__":
    unittest.main()
