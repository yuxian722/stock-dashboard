"""hourly_push.py 的離線單元測試(不連網)：用暫存SQLite驗證超時機台訊息格式
有帶上工程師/原因，以及稼動率統計邏輯。"""

import conftest  # noqa: F401  (設定 sys.path)

import datetime
import sqlite3
import tempfile
import unittest

import hourly_push


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
    conn.execute("CREATE TABLE utilization_record (UTIL TEXT, SETUP TEXT, ENTITY TEXT, fetched_at TEXT)")
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
        conn.execute("CREATE TABLE utilization_record (UTIL TEXT, SETUP TEXT, ENTITY TEXT, fetched_at TEXT)")
        conn.commit()
        conn.close()
        hourly_push.DB_PATH = path

        msg = hourly_push.build_hourly_push_message()
        self.assertIn("目前無進行中的改機/修機紀錄", msg)


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
