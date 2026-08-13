"""cpis_scraper.py 的離線單元測試(不連網)：Excel儲存格值轉換、報表列轉紀錄
(_rows_to_records，跟xlrd檔案I/O無關的純邏輯部分)。"""

import conftest  # noqa: F401  (設定 sys.path)

import datetime
import sqlite3
import tempfile
import unittest

import cpis_scraper


class TestFmtDate(unittest.TestCase):
    def test_datetime_value(self):
        self.assertEqual(cpis_scraper._fmt_date(datetime.datetime(2026, 8, 1, 7, 5)), "2026-08-01")

    def test_string_slash_format(self):
        self.assertEqual(cpis_scraper._fmt_date("2026/08/01"), "2026-08-01")

    def test_none_returns_none(self):
        self.assertIsNone(cpis_scraper._fmt_date(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(cpis_scraper._fmt_date(""))

    def test_unparseable_string_returns_none(self):
        self.assertIsNone(cpis_scraper._fmt_date("not a date"))


class TestFmtTime(unittest.TestCase):
    def test_time_value(self):
        self.assertEqual(cpis_scraper._fmt_time(datetime.time(7, 5, 30)), "07:05")

    def test_datetime_value(self):
        self.assertEqual(cpis_scraper._fmt_time(datetime.datetime(2026, 8, 1, 7, 5)), "07:05")

    def test_string_value(self):
        self.assertEqual(cpis_scraper._fmt_time("07:05:30"), "07:05")

    def test_none_returns_none(self):
        self.assertIsNone(cpis_scraper._fmt_time(None))


class TestToFloat(unittest.TestCase):
    def test_valid_number_string(self):
        self.assertEqual(cpis_scraper._to_float("1.5"), 1.5)

    def test_invalid_string_returns_none(self):
        self.assertIsNone(cpis_scraper._to_float("abc"))

    def test_none_returns_none(self):
        self.assertIsNone(cpis_scraper._to_float(None))


class TestCellStr(unittest.TestCase):
    def test_integer_valued_float_drops_decimal(self):
        self.assertEqual(cpis_scraper._cell_str(123.0), "123")

    def test_non_integer_float_kept(self):
        self.assertEqual(cpis_scraper._cell_str(1.5), "1.5")

    def test_none_returns_none(self):
        self.assertIsNone(cpis_scraper._cell_str(None))

    def test_blank_string_returns_none(self):
        self.assertIsNone(cpis_scraper._cell_str("  "))

    def test_strips_whitespace(self):
        self.assertEqual(cpis_scraper._cell_str("  BA205  "), "BA205")


def _make_row(machine_id="BA205", overrides=None):
    """組出一列28欄的假資料(對應_rows_to_records的欄位index對照)，方便測試覆蓋。"""
    row = [""] * 28
    row[0] = "B2"          # prod_line
    row[1] = "WD"           # oper
    row[2] = "2100SD"       # model
    row[3] = machine_id     # machine_id
    row[4] = datetime.datetime(2026, 8, 1)   # wait_date
    row[5] = datetime.time(7, 0)             # wait_time
    row[6] = datetime.datetime(2026, 8, 1)   # bgn_date
    row[7] = datetime.time(7, 5)             # bgn_time
    row[8] = datetime.datetime(2026, 8, 1)   # end_date
    row[9] = datetime.time(9, 20)            # end_time
    row[10] = 0.5    # wait_dur
    row[11] = 2.25   # dur
    row[12] = "ENG1"
    row[13] = ""
    row[14] = ""
    row[15] = "R"    # e_tag
    row[16] = "CED"  # job_code
    row[19] = "OWNER1"
    row[20] = "TOOL1"
    row[21] = "cause text"
    row[22] = "desc text"
    row[23] = "LOT123"
    row[24] = 2.3    # std
    row[26] = "BD1"
    row[27] = "PRODUCT1"
    for idx, val in (overrides or {}).items():
        row[idx] = val
    return row


HEADER_ROW = ["ProdLine", "OPER", "MODEL", "MACHINE", "WAIT_DATE", "WAIT_TIME"] + [""] * 22


class TestRowsToRecords(unittest.TestCase):
    def test_basic_mapping(self):
        rows = [[""], [""], [""], HEADER_ROW, _make_row()]
        records = cpis_scraper._rows_to_records(rows)
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["machine_id"], "BA205")
        self.assertEqual(r["prod_line"], "B2")
        self.assertEqual(r["model"], "2100SD")
        self.assertEqual(r["bgn_date"], "2026-08-01")
        self.assertEqual(r["bgn_time"], "07:05")
        self.assertEqual(r["end_time"], "09:20")
        self.assertEqual(r["wait_dur"], 0.5)
        self.assertEqual(r["dur"], 2.25)
        self.assertEqual(r["e_tag"], "R")
        self.assertEqual(r["job_code"], "CED")
        self.assertEqual(r["owner"], "OWNER1")
        self.assertEqual(r["tool_number"], "TOOL1")
        self.assertEqual(r["cause"], "cause text")
        self.assertEqual(r["description"], "desc text")
        self.assertEqual(r["con_lot_no"], "LOT123")
        self.assertEqual(r["std"], 2.3)
        self.assertEqual(r["bd_id"], "BD1")
        self.assertEqual(r["product"], "PRODUCT1")

    def test_engineer_id_prefers_last_non_blank_subfield(self):
        rows = [[""], [""], [""], HEADER_ROW, _make_row(overrides={12: "ENG1", 13: "ENG2", 14: ""})]
        records = cpis_scraper._rows_to_records(rows)
        self.assertEqual(records[0]["engineer_id"], "ENG2")

    def test_engineer_id_falls_back_to_first_subfield_when_rest_blank(self):
        rows = [[""], [""], [""], HEADER_ROW, _make_row(overrides={12: "ENG1", 13: "", 14: ""})]
        records = cpis_scraper._rows_to_records(rows)
        self.assertEqual(records[0]["engineer_id"], "ENG1")

    def test_header_row_detected_dynamically(self):
        # 表頭不在預設的index 3，也要能自動找到(用MACHINE/BGN關鍵字判斷)
        rows = [[""], HEADER_ROW, _make_row()]
        records = cpis_scraper._rows_to_records(rows)
        self.assertEqual(len(records), 1)

    def test_row_without_machine_id_skipped(self):
        rows = [[""], [""], [""], HEADER_ROW, _make_row(machine_id="")]
        records = cpis_scraper._rows_to_records(rows)
        self.assertEqual(records, [])

    def test_header_label_row_skipped(self):
        rows = [[""], [""], [""], HEADER_ROW, _make_row(machine_id="MACHINE")]
        records = cpis_scraper._rows_to_records(rows)
        self.assertEqual(records, [])

    def test_multiple_rows(self):
        rows = [[""], [""], [""], HEADER_ROW, _make_row(machine_id="BA205"), _make_row(machine_id="BA206")]
        records = cpis_scraper._rows_to_records(rows)
        self.assertEqual([r["machine_id"] for r in records], ["BA205", "BA206"])


class TestSaveToDbDedup(unittest.TestCase):
    """實測發現run_pipeline.py每小時重抓「昨天~今天」這個有重疊的區間，
    save_to_db()以前只有INSERT沒有DELETE，導致同一筆真實紀錄每小時都被
    重複塞進資料庫一次，累積下來讓依日期彙總的統計數字暴增到離譜的程度
    (實測過膨脹到2645次)。這裡鎖定：帶了date_start/date_end重複呼叫
    save_to_db()時，同一個查詢區間裡的資料是覆蓋，不是疊加。"""

    def setUp(self):
        self._orig_db_path = cpis_scraper.DB_PATH
        cpis_scraper.DB_PATH = tempfile.mktemp(suffix=".db")

    def tearDown(self):
        cpis_scraper.DB_PATH = self._orig_db_path

    def _record(self, machine_id="BA205", bgn_date="2026-08-09"):
        return {
            "prod_line": "APG", "oper": "DA", "model": "DIE-ATTACH", "machine_id": machine_id,
            "wait_date": None, "wait_time": None, "bgn_date": bgn_date, "bgn_time": "10:00",
            "end_date": bgn_date, "end_time": "12:00", "wait_dur": 0.0, "dur": 2.0,
            "engineer_id": "ENG1", "e_tag": "S", "job_code": "CED",
            "owner": "", "tool_number": "", "cause": "", "description": "",
            "con_lot_no": "", "std": None, "bd_id": "", "product": "",
        }

    def _count(self):
        conn = sqlite3.connect(cpis_scraper.DB_PATH)
        n = conn.execute("SELECT COUNT(*) FROM ee_maintenance_record").fetchone()[0]
        conn.close()
        return n

    def test_refetching_same_range_replaces_not_accumulates(self):
        cpis_scraper.save_to_db([self._record()], "20260808", "20260809")
        cpis_scraper.save_to_db([self._record()], "20260808", "20260809")
        cpis_scraper.save_to_db([self._record()], "20260808", "20260809")
        self.assertEqual(self._count(), 1)

    def test_records_outside_the_range_are_not_touched(self):
        # 8/1的紀錄不在這次重抓的8/8~8/9區間裡，不該被砍掉
        cpis_scraper.save_to_db([self._record(bgn_date="2026-08-01")], "20260801", "20260801")
        cpis_scraper.save_to_db([self._record(bgn_date="2026-08-09")], "20260808", "20260809")
        cpis_scraper.save_to_db([self._record(bgn_date="2026-08-09")], "20260808", "20260809")
        self.assertEqual(self._count(), 2)

    def test_no_date_range_given_keeps_old_append_only_behavior(self):
        # 沒帶date_start/date_end時維持舊行為(單純INSERT)，呼叫端沒給範圍
        # 就不冒然清資料，避免誤刪
        cpis_scraper.save_to_db([self._record()])
        cpis_scraper.save_to_db([self._record()])
        self.assertEqual(self._count(), 2)


class TestNormalizeHeader(unittest.TestCase):
    def test_strips_spaces_and_punctuation(self):
        self.assertEqual(cpis_scraper._normalize_header("ENGINEER ID."), "ENGINEERID")
        self.assertEqual(cpis_scraper._normalize_header("E.TAG"), "ETAG")
        self.assertEqual(cpis_scraper._normalize_header("JOB.CODE"), "JOBCODE")
        self.assertEqual(cpis_scraper._normalize_header("MACHINE ID"), "MACHINEID")

    def test_empty_or_none(self):
        self.assertEqual(cpis_scraper._normalize_header(""), "")
        self.assertEqual(cpis_scraper._normalize_header(None), "")


class TestExtractHhmm(unittest.TestCase):
    def test_extracts_time_from_combined_datetime_text(self):
        self.assertEqual(cpis_scraper._extract_hhmm("2026/08/11 09:17"), "09:17")

    def test_blank_returns_none(self):
        self.assertIsNone(cpis_scraper._extract_hhmm(""))
        self.assertIsNone(cpis_scraper._extract_hhmm(None))


class TestFirstToken(unittest.TestCase):
    def test_duplicated_engineer_id_takes_first(self):
        # 實測工號欄位會顯示"26163 26163"這種重複兩次的寫法
        self.assertEqual(cpis_scraper._first_token("26163 26163"), "26163")

    def test_single_token(self):
        self.assertEqual(cpis_scraper._first_token("S3145"), "S3145")

    def test_blank_returns_none(self):
        self.assertIsNone(cpis_scraper._first_token(""))
        self.assertIsNone(cpis_scraper._first_token(None))


class TestParseEeMaintenanceShiftHtml(unittest.TestCase):
    """
    2026/08/12使用者實測發現maintenance_record_r.aspx的shift查詢參數沒有
    真正被伺服器套用，改用真正有Shift篩選功能的maintenance_record_h.aspx
    表單頁面——這個頁面按下Fetch後把結果表格直接嵌在同一頁HTML回傳(不是
    XLS下載連結)，欄位是Production Line/MACHINE ID/WAIT-TIME/BGN-TIME/
    END-TIME/WAIT-DUR/DUR/ENGINEER ID./E.TAG/JOB.CODE/TOOL NUMBER/CAUSE
    (使用者截圖確認的實際表頭)。
    """

    def _html(self, rows_html):
        # 頁面上還有查詢表單本身的<table>(白名單制要能正確跳過，只挑出
        # 真正的資料表格)
        form_table = (
            "<table><tr><td>Date Range</td><td>Entity</td><td>Shift</td></tr>"
            "<tr><td>20260811</td><td>BA*</td><td>AD</td></tr></table>"
        )
        data_table = (
            "<table>"
            "<tr><th>Production Line</th><th>MACHINE ID</th><th>WAIT-TIME</th>"
            "<th>BGN-TIME</th><th>END-TIME</th><th>WAIT-DUR</th><th>DUR</th>"
            "<th>ENGINEER ID.</th><th>E.TAG</th><th>JOB.CODE</th>"
            "<th>TOOL NUMBER</th><th>CAUSE</th></tr>"
            + rows_html +
            "</table>"
        )
        return f"<html><body>{form_table}{data_table}</body></html>"

    def test_parses_data_rows_and_skips_form_table(self):
        rows_html = (
            "<tr><td>APG</td><td>BA205</td><td>2026/08/11 09:03</td>"
            "<td>2026/08/11 09:10</td><td>2026/08/11 09:17</td><td>0.12</td>"
            "<td>0.13</td><td>26163 26163</td><td>S</td><td>CWT</td>"
            "<td></td><td>CWT</td></tr>"
        )
        records = cpis_scraper.parse_ee_maintenance_shift_html(self._html(rows_html))

        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["machine_id"], "BA205")
        self.assertEqual(r["end_time"], "09:17")
        self.assertEqual(r["wait_dur"], 0.12)
        self.assertEqual(r["dur"], 0.13)
        self.assertEqual(r["engineer_id"], "26163")
        self.assertEqual(r["e_tag"], "S")
        self.assertEqual(r["job_code"], "CWT")

    def test_blank_wait_dur_becomes_none(self):
        rows_html = (
            "<tr><td>APG</td><td>BA205</td><td></td>"
            "<td>2026/08/11 10:04</td><td>2026/08/11 10:38</td><td></td>"
            "<td>0.58</td><td>18745 18745</td><td>S</td><td>INK</td>"
            "<td></td><td>WI INK 270EA</td></tr>"
        )
        records = cpis_scraper.parse_ee_maintenance_shift_html(self._html(rows_html))

        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["wait_dur"])

    def test_multiple_rows(self):
        rows_html = (
            "<tr><td>APG</td><td>BA205</td><td></td><td></td>"
            "<td>2026/08/11 09:17</td><td></td><td>0.13</td>"
            "<td>26163</td><td>S</td><td>CWT</td><td></td><td></td></tr>"
            "<tr><td>APG</td><td>BA207</td><td></td><td></td>"
            "<td>2026/08/11 12:14</td><td>0.31</td><td>0.50</td>"
            "<td>S3145</td><td>S</td><td>CSN</td><td></td><td></td></tr>"
        )
        records = cpis_scraper.parse_ee_maintenance_shift_html(self._html(rows_html))

        self.assertEqual(len(records), 2)
        self.assertEqual([r["machine_id"] for r in records], ["BA205", "BA207"])

    def test_no_matching_table_returns_empty_list(self):
        html = "<html><body><table><tr><td>不相關的表格</td></tr></table></body></html>"
        self.assertEqual(cpis_scraper.parse_ee_maintenance_shift_html(html), [])

    def test_row_with_missing_machine_id_is_skipped(self):
        rows_html = (
            "<tr><td>APG</td><td></td><td></td><td></td>"
            "<td>2026/08/11 09:17</td><td></td><td>0.13</td>"
            "<td>26163</td><td>S</td><td>CWT</td><td></td><td></td></tr>"
        )
        records = cpis_scraper.parse_ee_maintenance_shift_html(self._html(rows_html))
        self.assertEqual(records, [])

    def test_headers_rendered_as_sortable_submit_buttons_still_parse(self):
        # 2026/08/13使用者實測發現真正的根因：這個表格的表頭不是純文字，
        # 是ASP.NET GridView做成的可排序按鈕(<th><input type="submit"
        # value="MACHINE ID" .../></th>)，文字放在value屬性裡，<th>本身
        # 沒有文字節點——get_text()永遠抓到空字串，白名單比對永遠對不上，
        # 整個表格被當成「不是資料表格」跳過，變成「抓到真正有資料的表格、
        # 卻解析出0筆」(跟cpis_pm_monitor_scraper.py早就踩過的同一種坑)。
        form_table = (
            "<table><tr><td>Date Range</td><td>Entity</td><td>Shift</td></tr>"
            "<tr><td>20260811</td><td>BA*</td><td>AD</td></tr></table>"
        )
        data_table = (
            "<table>"
            '<tr><th><input type="submit" value="Production Line"/></th>'
            '<th><input type="submit" value="MACHINE ID"/></th>'
            '<th><input type="submit" value="WAIT-TIME"/></th>'
            '<th><input type="submit" value="BGN-TIME"/></th>'
            '<th><input type="submit" value="END-TIME"/></th>'
            '<th><input type="submit" value="WAIT-DUR"/></th>'
            '<th><input type="submit" value="DUR"/></th>'
            '<th><input type="submit" value="ENGINEER ID."/></th>'
            '<th><input type="submit" value="E.TAG"/></th>'
            '<th><input type="submit" value="JOB.CODE"/></th>'
            '<th><input type="submit" value="TOOL NUMBER"/></th>'
            '<th><input type="submit" value="CAUSE"/></th></tr>'
            "<tr><td>APG</td><td>BA205</td><td>2026/08/11 09:03</td>"
            "<td>2026/08/11 09:10</td><td>2026/08/11 09:17</td><td>0.12</td>"
            "<td>0.13</td><td>27376 27376</td><td>S</td><td>CEDO</td>"
            "<td></td><td></td></tr>"
            "</table>"
        )
        html = f"<html><body>{form_table}{data_table}</body></html>"

        records = cpis_scraper.parse_ee_maintenance_shift_html(html)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["machine_id"], "BA205")
        self.assertEqual(records[0]["job_code"], "CEDO")
        self.assertEqual(records[0]["engineer_id"], "27376")


if __name__ == "__main__":
    unittest.main()
