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
            machine_id TEXT, wait_date TEXT, wait_time TEXT, bgn_date TEXT, bgn_time TEXT,
            end_date TEXT, end_time TEXT, job_code TEXT, e_tag TEXT,
            engineer_id TEXT, cause TEXT
        )
    """)
    now = datetime.datetime.now()
    bgn = now - datetime.timedelta(hours=bgn_offset_hours)
    conn.execute(
        "INSERT INTO ee_maintenance_record VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (machine_id, None, None, bgn.strftime("%Y-%m-%d"), bgn.strftime("%H:%M"),
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
        # 預設e_tag="R"(修機)，修機才會顯示原因；改機沒有「原因」這個概念
        hourly_push.DB_PATH = _make_db_with_ongoing_record(
            engineer_id="ENG42", cause="Bad identification value setting"
        )
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("人員ENG42", msg)
        self.assertIn("原因:Bad identification value setting", msg)

    def test_missing_engineer_and_cause_show_placeholder(self):
        hourly_push.DB_PATH = _make_db_with_ongoing_record(engineer_id=None, cause=None)
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("人員未指定", msg)
        self.assertIn("原因:無", msg)

    def test_no_ongoing_records_shows_placeholder_message(self):
        path = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE ee_maintenance_record (
                machine_id TEXT, wait_date TEXT, wait_time TEXT, bgn_date TEXT, bgn_time TEXT,
                end_date TEXT, end_time TEXT, job_code TEXT, e_tag TEXT,
                engineer_id TEXT, cause TEXT
            )
        """)
        conn.execute(_UTIL_TABLE_SQL)
        conn.commit()
        conn.close()
        hourly_push.DB_PATH = path

        msg = hourly_push.build_hourly_push_message()
        self.assertIn("目前無進行中/等待中的改機/修機紀錄", msg)


def _make_db_with_group_rates(rows):
    """rows是list of dict，每個dict至少要有model/entity，其餘欄位(util/w_set/setup/
    eng/pm/w_rep/in_rep)缺的話當NULL，全部寫進utilization_record同一批fetched_at。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            machine_id TEXT, wait_date TEXT, wait_time TEXT, bgn_date TEXT, bgn_time TEXT,
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


class TestGroupForMachine(unittest.TestCase):
    """機台代號→機型群組(ESEC/DB/LOC/FC)，對齊同事Dashboard的getEntityGroup規則。"""

    def test_esec_prefixes(self):
        self.assertEqual(hourly_push._group_for_machine("BA205"), "ESEC")
        self.assertEqual(hourly_push._group_for_machine("BA401"), "ESEC")

    def test_db_prefixes(self):
        self.assertEqual(hourly_push._group_for_machine("BAA01"), "DB")
        self.assertEqual(hourly_push._group_for_machine("BAB05"), "DB")
        self.assertEqual(hourly_push._group_for_machine("BA701"), "DB")

    def test_loc_prefix(self):
        self.assertEqual(hourly_push._group_for_machine("BA801"), "LOC")

    def test_fc_prefixes(self):
        self.assertEqual(hourly_push._group_for_machine("BA512"), "FC")
        self.assertEqual(hourly_push._group_for_machine("BAD01"), "FC")

    def test_unknown_prefix_returns_none(self):
        self.assertIsNone(hourly_push._group_for_machine("XY999"))

    def test_empty_returns_none(self):
        self.assertIsNone(hourly_push._group_for_machine(""))
        self.assertIsNone(hourly_push._group_for_machine(None))


def _make_db_with_records(rows):
    """rows是list of dict，每個dict可包含machine_id/wait_date/wait_time/bgn_date/
    bgn_time/end_date/end_time/job_code/e_tag/engineer_id/cause，缺的欄位當NULL。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            machine_id TEXT, wait_date TEXT, wait_time TEXT, bgn_date TEXT, bgn_time TEXT,
            end_date TEXT, end_time TEXT, job_code TEXT, e_tag TEXT,
            engineer_id TEXT, cause TEXT
        )
    """)
    conn.execute(_UTIL_TABLE_SQL)
    cols = ["machine_id", "wait_date", "wait_time", "bgn_date", "bgn_time",
            "end_date", "end_time", "job_code", "e_tag", "engineer_id", "cause"]
    for row in rows:
        conn.execute(
            f"INSERT INTO ee_maintenance_record ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            tuple(row.get(c) for c in cols),
        )
    conn.commit()
    conn.close()
    return path


class TestGetSetupGroupStats(unittest.TestCase):
    """今日改機統計：wait_date/wait_time這些欄位資料庫裡本來就有存(EJP報表解析
    時就寫進去了)，只是之前的整點推播沒有查詢/顯示過。這裡鎖定done(今日完成)/
    in_progress(改機中)/waiting(待改)三種狀態依機台代號正確分類到ESEC/DB/LOC/FC。"""

    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_classifies_done_in_progress_waiting_by_group(self):
        today = datetime.date.today().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "bgn_date": "2026-08-08", "bgn_time": "10:00",
             "end_date": today, "end_time": "12:00", "job_code": "CED"},   # ESEC, 今日完成
            {"machine_id": "BAA01", "e_tag": "S", "bgn_date": "2026-08-09", "bgn_time": "10:00",
             "job_code": "CED"},                                          # DB, 改機中
            {"machine_id": "BA801", "e_tag": "S", "wait_date": "2026-08-09", "wait_time": "09:00",
             "job_code": "CED"},                                          # LOC, 待改
            # e_tag=R的紀錄不該被算進改機統計
            {"machine_id": "BA512", "e_tag": "R", "bgn_date": "2026-08-09", "bgn_time": "10:00",
             "job_code": "CE"},
        ])
        stats = hourly_push.get_setup_group_stats()
        self.assertEqual(stats["ESEC"], {"done": 1, "in_progress": 0, "waiting": 0})
        self.assertEqual(stats["DB"], {"done": 0, "in_progress": 1, "waiting": 0})
        self.assertEqual(stats["LOC"], {"done": 0, "in_progress": 0, "waiting": 1})
        self.assertEqual(stats["FC"], {"done": 0, "in_progress": 0, "waiting": 0})

    def test_completed_on_a_different_day_not_counted_as_done_today(self):
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "bgn_date": "2026-08-01", "bgn_time": "10:00",
             "end_date": "2026-08-01", "end_time": "12:00", "job_code": "CED"},
        ])
        stats = hourly_push.get_setup_group_stats()
        self.assertEqual(stats["ESEC"]["done"], 0)


class TestGetWaitingRecords(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_only_returns_records_that_have_not_started_yet(self):
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA801", "e_tag": "S", "wait_date": "2026-08-09", "wait_time": "09:00",
             "job_code": "CED"},                                          # 待改：只有wait，符合
            {"machine_id": "BA205", "e_tag": "S", "wait_date": "2026-08-09", "wait_time": "08:00",
             "bgn_date": "2026-08-09", "bgn_time": "09:00", "job_code": "CED"},  # 已經開始動工了，不算待改
            {"machine_id": "BA512", "e_tag": "R", "wait_date": "2026-08-09", "wait_time": "07:00",
             "job_code": "CE"},                                           # 待修
        ])
        rows = hourly_push.get_waiting_records()
        machine_ids = {r["machine_id"] for r in rows}
        self.assertEqual(machine_ids, {"BA801", "BA512"})


class TestBuildHourlyPushMessageNewSections(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_includes_setup_stats_and_waiting_sections(self):
        today = datetime.date.today().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "bgn_date": "2026-08-08", "bgn_time": "10:00",
             "end_date": today, "end_time": "12:00", "job_code": "CED"},
            {"machine_id": "BA801", "e_tag": "S", "wait_date": "2026-08-09", "wait_time": "09:00",
             "job_code": "CED"},
        ])
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("🔧 今日改機統計", msg)
        self.assertIn("EPOXY   改機1 | 改機中0 | 待改0", msg)
        self.assertIn("⏳待改", msg)
        self.assertIn("BA801  待改", msg)


if __name__ == "__main__":
    unittest.main()
