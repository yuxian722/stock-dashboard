"""cpis_scraper.py 的離線單元測試(不連網)：Excel儲存格值轉換、報表列轉紀錄
(_rows_to_records，跟xlrd檔案I/O無關的純邏輯部分)。"""

import conftest  # noqa: F401  (設定 sys.path)

import datetime
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


if __name__ == "__main__":
    unittest.main()
