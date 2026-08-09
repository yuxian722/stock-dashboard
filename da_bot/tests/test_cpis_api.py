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


class TestCheckEntityPattern(unittest.TestCase):
    def test_two_literal_chars_ok(self):
        cpis_api._check_entity_pattern("BA*")  # 不應該丟例外

    def test_full_code_without_wildcard_ok(self):
        cpis_api._check_entity_pattern("BA205")  # 不應該丟例外

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            cpis_api._check_entity_pattern("")

    def test_none_rejected(self):
        with self.assertRaises(ValueError):
            cpis_api._check_entity_pattern(None)

    def test_single_literal_char_rejected(self):
        # CPIS實測會被擋："B*"扣掉萬用字元只剩1個字元
        with self.assertRaises(ValueError):
            cpis_api._check_entity_pattern("B*")

    def test_only_wildcards_rejected(self):
        with self.assertRaises(ValueError):
            cpis_api._check_entity_pattern("*")


class TestIframeSrcs(unittest.TestCase):
    def test_extracts_frame_and_iframe(self):
        html = '<frame src="/a.aspx"></frame><iframe src="/b.aspx"></iframe>'
        self.assertEqual(cpis_api._iframe_srcs(html), ["/a.aspx", "/b.aspx"])

    def test_no_frames_returns_empty(self):
        self.assertEqual(cpis_api._iframe_srcs("<div>no frames</div>"), [])


class TestEeOperIndexFromForm(unittest.TestCase):
    def test_name_then_value_order(self):
        html = '<input type="checkbox" name="DropDownCheckBoxes1$7" value="DA" />'
        self.assertEqual(cpis_api._ee_oper_index_from_form(html), {"DA": 7})

    def test_value_then_name_order(self):
        html = '<input type="checkbox" value="DA" name="DropDownCheckBoxes1$7" />'
        self.assertEqual(cpis_api._ee_oper_index_from_form(html), {"DA": 7})

    def test_no_matches_returns_empty(self):
        self.assertEqual(cpis_api._ee_oper_index_from_form("<div>nothing here</div>"), {})


class TestEeOperFields(unittest.TestCase):
    def test_single_code_uses_index(self):
        fields = cpis_api._ee_oper_fields("", "DA")
        self.assertEqual(fields, {"DropDownCheckBoxes1$7": "DA"})

    def test_multiple_comma_separated_codes(self):
        fields = cpis_api._ee_oper_fields("", "DA,PRT")
        self.assertEqual(fields, {"DropDownCheckBoxes1$7": "DA", "DropDownCheckBoxes1$48": "PRT"})

    def test_all_selects_every_known_code(self):
        fields = cpis_api._ee_oper_fields("", "ALL")
        self.assertEqual(len(fields), len(cpis_api._EE_OPER_INDEX))
        self.assertEqual(fields["DropDownCheckBoxes1$7"], "DA")

    def test_wildcard_star_also_selects_all(self):
        fields = cpis_api._ee_oper_fields("", "*")
        self.assertEqual(len(fields), len(cpis_api._EE_OPER_INDEX))

    def test_unknown_code_skipped_silently(self):
        fields = cpis_api._ee_oper_fields("", "NOT_A_REAL_CODE")
        self.assertEqual(fields, {})

    def test_form_html_overrides_fallback_index(self):
        html = '<input type="checkbox" name="DropDownCheckBoxes1$99" value="DA" />'
        fields = cpis_api._ee_oper_fields(html, "DA")
        self.assertEqual(fields, {"DropDownCheckBoxes1$99": "DA"})


if __name__ == "__main__":
    unittest.main()
