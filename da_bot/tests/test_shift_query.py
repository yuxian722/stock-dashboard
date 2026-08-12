"""shift_query.py 的離線單元測試(不連網)：mock掉cpis_api/cpis_scraper，
驗證即時班別(AD/AN/BD/BN)改機查詢的篩選+組字邏輯，不實際打CPIS。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

import cpis_api
import cpis_scraper
import shift_query


class TestIsValidShift(unittest.TestCase):
    def test_valid_shifts(self):
        for s in ("AD", "AN", "BD", "BN", "ad", "an", "bd", "bn"):
            self.assertTrue(shift_query.is_valid_shift(s), msg=s)

    def test_invalid_shifts(self):
        for s in ("", None, "None", "AB", "CD", "改機"):
            self.assertFalse(shift_query.is_valid_shift(s), msg=s)


class TestLiveGroupShiftChangeoverReply(unittest.TestCase):
    """
    2026/08/12使用者實測發現：原本用的maintenance_record_r.aspx雖然接受
    shift查詢參數，但伺服器端根本沒有真正套用(shift=AD查出來的筆數跟
    shift=None一模一樣)。改用真正有Shift篩選功能的maintenance_record_h.aspx
    表單頁面(cpis_api.fetch_ee_maintenance_shift_html())，回傳HTML表格
    (不是XLS)，要用cpis_scraper.parse_ee_maintenance_shift_html()解析。
    """

    def setUp(self):
        self._orig_fetch = cpis_api.fetch_ee_maintenance_shift_html
        self._orig_parse = cpis_scraper.parse_ee_maintenance_shift_html

    def tearDown(self):
        cpis_api.fetch_ee_maintenance_shift_html = self._orig_fetch
        cpis_scraper.parse_ee_maintenance_shift_html = self._orig_parse

    def test_fetches_with_correct_shift_and_date_params(self):
        captured = {}

        def fake_fetch(date_start, date_end, entity="BA*", shift="AD", jobcode="", etag="S"):
            captured["date_start"] = date_start
            captured["date_end"] = date_end
            captured["entity"] = entity
            captured["shift"] = shift
            captured["etag"] = etag
            return "<html></html>"

        cpis_api.fetch_ee_maintenance_shift_html = fake_fetch
        cpis_scraper.parse_ee_maintenance_shift_html = lambda html: []

        shift_query.live_group_shift_changeover_reply("DB", "ad", "20260811", "08/11")

        self.assertEqual(captured["date_start"], "20260811")
        self.assertEqual(captured["date_end"], "20260811")
        self.assertEqual(captured["entity"], "BA*")
        self.assertEqual(captured["shift"], "AD")  # 要轉大寫傳給CPIS
        self.assertEqual(captured["etag"], "S")  # 只要改機完成(e_tag=S)的紀錄

    def test_filters_to_group_and_formats_report(self):
        cpis_api.fetch_ee_maintenance_shift_html = lambda *a, **k: "<html>fake</html>"
        cpis_scraper.parse_ee_maintenance_shift_html = lambda html: [
            {"machine_id": "BAA01", "job_code": "CED", "e_tag": "S",
             "engineer_id": "s10435", "dur": 1.0, "wait_dur": 0.5, "end_time": "10:00"},
            {"machine_id": "BA801", "job_code": "CN", "e_tag": "S",  # LOC，不是DB
             "engineer_id": "s10488", "dur": 2.0, "wait_dur": 1.0, "end_time": "11:00"},
            {"machine_id": "BAA02", "job_code": "CED", "e_tag": "R",  # 修機不是改機
             "engineer_id": "s10435", "dur": 3.0, "wait_dur": 1.0, "end_time": "12:00"},
        ]

        reply = shift_query.live_group_shift_changeover_reply("DB", "AD", "20260811", "08/11")

        self.assertIn("【DB改機】08/11（A班早班）共1台", reply)
        self.assertIn("BAA01", reply)
        self.assertNotIn("BA801", reply)
        self.assertNotIn("BAA02", reply)

    def test_no_records_for_shift_returns_message(self):
        cpis_api.fetch_ee_maintenance_shift_html = lambda *a, **k: "<html>empty</html>"
        cpis_scraper.parse_ee_maintenance_shift_html = lambda html: []

        reply = shift_query.live_group_shift_changeover_reply("DB", "BN", "20260811", "08/11")
        self.assertIn("目前沒有完成的改機紀錄", reply)

    def test_cpis_fetch_failure_returns_error_message_not_exception(self):
        def fake_fetch(*a, **k):
            raise RuntimeError("連線逾時")

        cpis_api.fetch_ee_maintenance_shift_html = fake_fetch

        reply = shift_query.live_group_shift_changeover_reply("DB", "AD", "20260811", "08/11")
        self.assertIn("即時查詢CPIS失敗", reply)
        self.assertIn("連線逾時", reply)


if __name__ == "__main__":
    unittest.main()
