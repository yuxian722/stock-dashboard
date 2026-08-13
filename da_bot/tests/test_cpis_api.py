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


class TestEeQueryString(unittest.TestCase):
    def test_contains_dates_and_entity(self):
        qs = cpis_api._ee_query_string("20260801", "20260809", "BA*", "*")
        self.assertIn("start_date=20260801", qs)
        self.assertIn("end_date=20260809", qs)
        self.assertIn("entity=BA*", qs)
        self.assertIn("jobcode=*", qs)

    def test_jobcode_defaults_to_empty(self):
        qs = cpis_api._ee_query_string("20260801", "20260809", "BA*")
        self.assertIn("jobcode=&", qs)

    def test_shift_defaults_to_none(self):
        qs = cpis_api._ee_query_string("20260801", "20260809", "BA*")
        self.assertIn("ddl_shift=None", qs)

    def test_shift_can_be_overridden(self):
        # 2026/08/10使用者要求：CPIS查詢頁的Shift下拉選單值是AD/AN/BD/BN
        # (A/B班組×早/夜班)，這是查詢時的過濾參數(不是回傳資料裡的欄位)。
        # 2026/08/13使用者實測發現真正的查詢字串參數名稱是"ddl_shift="
        # (跟maintenance_record_h.aspx表單的DOM欄位名稱一致)，不是"shift="
        # ——這是之前誤判這個端點"沒有實作Shift篩選"的根因。
        qs = cpis_api._ee_query_string("20260801", "20260809", "BA*", shift="AD")
        self.assertIn("ddl_shift=AD", qs)


class TestFindEjpUrl(unittest.TestCase):
    def test_finds_full_url(self):
        html = '<a href="http://tncpisapg.tn.chipmos.com.tw/APG/assyfab/cpis/report/EJP_20260803192310.xls">Here</a>'
        self.assertEqual(
            cpis_api._find_ejp_url(html),
            "http://tncpisapg.tn.chipmos.com.tw/APG/assyfab/cpis/report/EJP_20260803192310.xls",
        )

    def test_finds_backslash_relative_href_via_id_fallback(self):
        # CPIS實際回傳的是Windows風格反斜線相對路徑，不是正規URL，
        # 前兩個regex比對不到，要靠只抓EJP編號的第三層備援重組出正確網址
        html = r'<a href="\APG\assyfab\cpis\report\EJP_20260803192310.xls">Here</a>'
        self.assertEqual(
            cpis_api._find_ejp_url(html),
            "http://tncpisapg.tn.chipmos.com.tw/APG/assyfab/cpis/report/EJP_20260803192310.xls",
        )

    def test_no_match_returns_none(self):
        self.assertIsNone(cpis_api._find_ejp_url("<div>no report link here</div>"))


if __name__ == "__main__":
    unittest.main()
