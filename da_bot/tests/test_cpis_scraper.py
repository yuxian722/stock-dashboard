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


if __name__ == "__main__":
    unittest.main()
