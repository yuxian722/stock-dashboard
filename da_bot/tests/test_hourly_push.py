"""hourly_push.py 的離線單元測試(不連網)：用暫存SQLite驗證超時機台訊息格式
有帶上工程師/原因，以及官方GROUP分組稼動率統計邏輯。"""

import conftest  # noqa: F401  (設定 sys.path)

import datetime
import sqlite3
import tempfile
import unittest

import hourly_push

_UTIL_TABLE_SQL = """
    CREATE TABLE utilization_record (
        MODEL TEXT, ENTITY TEXT, fetched_at TEXT,
        UTIL TEXT, "W-SET" TEXT, SETUP TEXT, ENG TEXT, PM TEXT,
        "W-REP" TEXT, "IN-REP" TEXT
    )
"""


def _make_db_with_ongoing_record(machine_id="BA205", bgn_offset_hours=5.0, job_code="CED",
                                  e_tag="R", engineer_id="ENG1", cause="cause text"):
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            machine_id TEXT, bgn_date TEXT, bgn_time TEXT,
            end_date TEXT, end_time TEXT, job_code TEXT, e_tag TEXT,
            engineer_id TEXT, cause TEXT
        )
    """)
    now = datetime.datetime.now()
    bgn = now - datetime.timedelta(hours=bgn_offset_hours)
    conn.execute(
        "INSERT INTO ee_maintenance_record VALUES (?,?,?,?,?,?,?,?,?)",
        (machine_id, bgn.strftime("%Y-%m-%d"), bgn.strftime("%H:%M"),
         None, None, job_code, e_tag, engineer_id, cause),
    )
    conn.execute(_UTIL_TABLE_SQL)
    conn.commit()
    conn.close()
    return path


class TestBuildHourlyPushMessage(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_overtime_line_includes_engineer_and_cause(self):
        hourly_push.DB_PATH = _make_db_with_ongoing_record(
            engineer_id="ENG42", cause="Bad identification value setting"
        )
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("工程師:ENG42", msg)
        self.assertIn("原因:Bad identification value setting", msg)

    def test_missing_engineer_and_cause_show_placeholder(self):
        hourly_push.DB_PATH = _make_db_with_ongoing_record(engineer_id=None, cause=None)
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("工程師:未指定", msg)
        self.assertIn("原因:無", msg)

    def test_no_ongoing_records_shows_placeholder_message(self):
        path = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE ee_maintenance_record (
                machine_id TEXT, bgn_date TEXT, bgn_time TEXT,
                end_date TEXT, end_time TEXT, job_code TEXT, e_tag TEXT,
                engineer_id TEXT, cause TEXT
            )
        """)
        conn.execute(_UTIL_TABLE_SQL)
        conn.commit()
        conn.close()
        hourly_push.DB_PATH = path

        msg = hourly_push.build_hourly_push_message()
        self.assertIn("目前無進行中的改機/修機紀錄", msg)


def _make_db_with_group_rates(rows):
    """rows是list of dict，每個dict至少要有model/entity，其餘欄位(util/w_set/setup/
    eng/pm/w_rep/in_rep)缺的話當NULL，全部寫進utilization_record同一批fetched_at。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            machine_id TEXT, bgn_date TEXT, bgn_time TEXT,
            end_date TEXT, end_time TEXT, job_code TEXT, e_tag TEXT,
            engineer_id TEXT, cause TEXT
        )
    """)
    conn.execute(_UTIL_TABLE_SQL)
    fetched_at = "2026-08-09T12:00:00"
    for row in rows:
        conn.execute(
            'INSERT INTO utilization_record (MODEL, ENTITY, fetched_at, UTIL, "W-SET", SETUP, ENG, PM, "W-REP", "IN-REP")'
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                row.get("model"), row.get("entity"), fetched_at,
                row.get("util"), row.get("w_set"), row.get("setup"),
                row.get("eng"), row.get("pm"), row.get("w_rep"), row.get("in_rep"),
            ),
        )
    conn.commit()
    conn.close()
    return path


class TestGetOfficialGroupRates(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_reads_official_group_row_all_fields(self):
        hourly_push.DB_PATH = _make_db_with_group_rates([
            {"model": "DB800", "entity": None, "util": "77.0 %", "w_set": "1.1 %",
             "setup": "13.0 %", "eng": "0.0 %", "pm": "0.0 %", "w_rep": "1.7 %", "in_rep": "1.3 %"},
            # 個別機台明細列(ENTITY非空)，不該被拿來當官方GROUP數字
            {"model": "DB800", "entity": "BAA01", "util": "50.0 %"},
        ])
        rates = hourly_push.get_official_group_rates()
        self.assertEqual(
            rates["DB800"],
            {"UTIL": 77.0, "W-SET": 1.1, "SETUP": 13.0, "ENG": 0.0, "PM": 0.0, "W-REP": 1.7, "IN-REP": 1.3},
        )

    def test_missing_group_omitted_from_result(self):
        hourly_push.DB_PATH = _make_db_with_group_rates([{"model": "DB800", "entity": None, "util": "77.0 %"}])
        rates = hourly_push.get_official_group_rates()
        self.assertNotIn("Epoxy", rates)

    def test_empty_table_returns_empty_dict(self):
        hourly_push.DB_PATH = _make_db_with_group_rates([])
        self.assertEqual(hourly_push.get_official_group_rates(), {})

    def test_push_message_lists_each_group_and_flags_missing(self):
        hourly_push.DB_PATH = _make_db_with_group_rates([
            {"model": "DB800", "entity": None, "util": "77.0 %", "setup": "13.0 %"},
        ])
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("DB800  稼動77.0% 改機13.0%", msg)
        self.assertIn("Epoxy: 暫無資料", msg)

    def test_push_message_skips_missing_fields_within_a_group(self):
        # w_set/eng/pm/w_rep/in_rep都沒抓到值時，該欄位不該出現在那一行裡
        hourly_push.DB_PATH = _make_db_with_group_rates([
            {"model": "DB800", "entity": None, "util": "77.0 %"},
        ])
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("DB800  稼動77.0%", msg)
        self.assertNotIn("改機", msg.split("DB800")[1].split("\n")[0])


class TestGetStdHours(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(hourly_push.get_std_hours("CED"), 2.3)

    def test_prefix_match(self):
        self.assertEqual(hourly_push.get_std_hours("CEE123"), 3.0)

    def test_unknown_returns_none(self):
        self.assertIsNone(hourly_push.get_std_hours("XYZ"))

    def test_empty_returns_none(self):
        self.assertIsNone(hourly_push.get_std_hours(""))


if __name__ == "__main__":
    unittest.main()
