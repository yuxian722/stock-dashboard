"""hourly_push.py 的離線單元測試(不連網)：驗證今日改機統計(EE Maintenance完成數
+PM Monitor改機中/待改數+EPOXY依job_code分類)、即時機況機台明細、PM Monitor
JCODE超時判斷、以及官方GROUP分組稼動率統計邏輯。"""

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


def _add_pm_monitor_rows(db_path, rows, fetched_at="2026-08-09T17:00:00"):
    """rows是list，每個元素可以是(entity, status)這種2-tuple(其餘欄位當NULL)，
    也可以是包含entity/status/jcode/operator/in_time等鍵的dict(缺的鍵當NULL)，
    寫進db_path的pm_monitor_record表(同一批fetched_at)。"""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pm_monitor_record (
            oper TEXT, entity TEXT, model TEXT, status TEXT,
            lot_no TEXT, bond_id TEXT, wip TEXT, in_time TEXT,
            outplan TEXT, jcode TEXT, operator TEXT, fetched_at TEXT
        )
    """)
    for row in rows:
        if isinstance(row, dict):
            d = row
        else:
            entity, status = row
            d = {"entity": entity, "status": status}
        conn.execute(
            "INSERT INTO pm_monitor_record (entity, status, jcode, operator, in_time, fetched_at)"
            " VALUES (?,?,?,?,?,?)",
            (d.get("entity"), d.get("status"), d.get("jcode"), d.get("operator"), d.get("in_time"), fetched_at),
        )
    conn.commit()
    conn.close()


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


class TestGetEpoxyDoneByJcode(unittest.TestCase):
    """EPOXY(ESEC+DB)今日已完成的改機次數，依「實際job_code」逐一列出台數。"""

    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_groups_by_actual_jcode_not_broad_category(self):
        # 2026/08/09使用者要求要看到CED/CEDO/CD這些「實際代碼」各自的台數，
        # 不要收斂成CED機台/CEE機台/CD機台三個大類，把CEDO藏在CED機台裡看不到
        today = datetime.date.today().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "job_code": "CED"},    # ESEC
            {"machine_id": "BA401", "e_tag": "S", "end_date": today, "job_code": "CEDO"},   # ESEC
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "job_code": "CEDO"},   # DB
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "job_code": "CD"},     # DB
            {"machine_id": "BA801", "e_tag": "S", "end_date": today, "job_code": "CED"},    # LOC, 不算EPOXY
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "job_code": "INK"},    # ESEC，但INK不是真正改機，不算
        ])
        result = hourly_push.get_epoxy_done_by_jcode()
        self.assertEqual(result, {"CED": 1, "CEDO": 2, "CD": 1})
        self.assertEqual(sum(result.values()), 4)

    def test_non_changeover_jcode_not_counted(self):
        # CPIS的e_tag='S'不是每一筆都是機型改機，補墨水(INK)/AI視覺校正(AING)/
        # 換料(CWT)/操作員備註(OC)這類生產中的小動作也會被標成'S'，
        # 2026/08/09使用者確認這些不算改機，一律不計入(也不放進"其他"桶)
        today = datetime.date.today().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "job_code": "INK"},
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "job_code": "AING"},
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "job_code": "CWT"},
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "job_code": "OC"},
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(), {})

    def test_non_epoxy_group_excluded(self):
        today = datetime.date.today().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA801", "e_tag": "S", "end_date": today, "job_code": "CED"},  # LOC
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(), {})

    def test_e_tag_r_excluded(self):
        today = datetime.date.today().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "R", "end_date": today, "job_code": "CED"},
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(), {})

    def test_not_completed_today_excluded(self):
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": "2026-08-01", "job_code": "CED"},
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(), {})


class TestGetSetupGroupStats(unittest.TestCase):
    """今日改機統計：done(今日完成)算自ee_maintenance_record；in_progress(改機中)/
    waiting(待改)改成算自pm_monitor_record的即時快照(SETUP/WAIT-SETUP)。"""

    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_done_from_ee_maintenance_in_progress_and_waiting_from_pm_monitor(self):
        today = datetime.date.today().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "bgn_date": "2026-08-08", "bgn_time": "10:00",
             "end_date": today, "end_time": "12:00", "job_code": "CED"},   # ESEC, 今日完成
            # e_tag=R的紀錄不該被算進改機統計
            {"machine_id": "BA512", "e_tag": "R", "bgn_date": "2026-08-09", "bgn_time": "10:00",
             "job_code": "CE"},
        ])
        _add_pm_monitor_rows(hourly_push.DB_PATH, [
            ("BAA01", "SETUP"),       # DB, 改機中
            ("BA801", "WAIT-SETUP"),  # LOC, 待改
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

    def test_non_changeover_jcode_not_counted_as_done(self):
        # e_tag='S'裡INK(補墨水)/AING(AI視覺校正)這類生產中的小動作不算改機，
        # 2026/08/09使用者確認只算CED/CEE/CD三類，其餘一律不計入done
        today = datetime.date.today().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "job_code": "INK"},
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "job_code": "AING"},
        ])
        stats = hourly_push.get_setup_group_stats()
        self.assertEqual(stats["ESEC"]["done"], 0)

    def test_no_pm_monitor_table_leaves_in_progress_and_waiting_zero(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        stats = hourly_push.get_setup_group_stats()
        for g in ("ESEC", "DB", "LOC", "FC"):
            self.assertEqual(stats[g], {"done": 0, "in_progress": 0, "waiting": 0})


class TestBuildHourlyPushMessageNewSections(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_includes_setup_stats_and_epoxy_jcode_breakdown(self):
        today = datetime.date.today().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "bgn_date": "2026-08-08", "bgn_time": "10:00",
             "end_date": today, "end_time": "12:00", "job_code": "CED"},
            {"machine_id": "BAA01", "e_tag": "S", "bgn_date": "2026-08-08", "bgn_time": "10:00",
             "end_date": today, "end_time": "12:00", "job_code": "CEDO"},
        ])
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("🔧 今日改機統計", msg)
        self.assertIn("EPOXY   改機2 | 改機中0 | 待改0", msg)
        # 逐一列出實際job_code(不再收斂成CED機台這種大類)，加總要等於改機總數
        self.assertIn("CED1台", msg)
        self.assertIn("CEDO1台", msg)

    def test_overtime_section_shows_placeholder_when_no_pm_monitor_data(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("⏰ 超時機台", msg)
        self.assertIn("目前無超過標準工時的機台，或PM Monitor資料尚未抓取", msg)


class TestGetPmMonitorGroupStats(unittest.TestCase):
    """PM/REPAIR/SETUP Monitor即時機況(cpis_pm_monitor_scraper.py抓的真實
    快照)依機型群組統計，跟get_setup_group_stats()的EE Maintenance推算值
    是兩個獨立的資料來源。"""

    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_groups_by_machine_prefix_and_counts_status(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        _add_pm_monitor_rows(hourly_push.DB_PATH, [
            ("BA205", "IN-REPAIR"),  # ESEC
            ("BAA01", "SETUP"),      # DB
            ("BAA02", "SETUP"),      # DB
            ("BA801", "WAIT-SETUP"),  # LOC
        ])
        stats = hourly_push.get_pm_monitor_group_stats()
        self.assertEqual(stats["ESEC"], {"IN-REPAIR": 1})
        self.assertEqual(stats["DB"], {"SETUP": 2})
        self.assertEqual(stats["LOC"], {"WAIT-SETUP": 1})
        self.assertEqual(stats["FC"], {})

    def test_missing_table_returns_empty_group_dict(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        self.assertEqual(
            hourly_push.get_pm_monitor_group_stats(),
            {"ESEC": {}, "DB": {}, "LOC": {}, "FC": {}},
        )

    def test_push_message_includes_pm_monitor_section_when_data_available(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        _add_pm_monitor_rows(hourly_push.DB_PATH, [("BA205", "IN-REPAIR")])
        msg = hourly_push.build_hourly_push_message()
        self.assertIn("⚡ 即時機況(PM Monitor)", msg)
        self.assertIn("EPOXY  修機中1", msg)
        self.assertIn("├ESEC  修機中1", msg)
        self.assertIn("機台明細:", msg)

    def test_push_message_skips_pm_monitor_status_section_when_no_data(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        msg = hourly_push.build_hourly_push_message()
        self.assertNotIn("⚡ 即時機況(PM Monitor)", msg)
        self.assertNotIn("機台明細", msg)


class TestGetPmMonitorRecords(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_missing_table_returns_empty_list(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        self.assertEqual(hourly_push.get_pm_monitor_records(), [])

    def test_only_returns_latest_fetched_at_batch(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        _add_pm_monitor_rows(hourly_push.DB_PATH, [("BA205", "IN-REPAIR")], fetched_at="2026-08-09T16:00:00")
        _add_pm_monitor_rows(hourly_push.DB_PATH, [("BAA01", "SETUP")], fetched_at="2026-08-09T17:00:00")
        rows = hourly_push.get_pm_monitor_records()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entity"], "BAA01")


class TestGetPmJcodeStdHours(unittest.TestCase):
    """PM Monitor JCODE的標準工時對照表，跟query_bot.py/舊hourly_push.py的
    EE Maintenance用JOB_CODE_STD_HOURS是不同體系，這裡鎖定使用者2026/08/09
    提供的PM Monitor專用數字。"""

    def test_exact_matches(self):
        self.assertEqual(hourly_push.get_pm_jcode_std_hours("CED-1"), 2.3)
        self.assertEqual(hourly_push.get_pm_jcode_std_hours("CED"), 2.3)
        self.assertEqual(hourly_push.get_pm_jcode_std_hours("CEDO"), 2.3)
        self.assertEqual(hourly_push.get_pm_jcode_std_hours("CEE"), 4.17)
        self.assertEqual(hourly_push.get_pm_jcode_std_hours("CN"), 3.38)
        self.assertEqual(hourly_push.get_pm_jcode_std_hours("CD"), 0.5)
        self.assertEqual(hourly_push.get_pm_jcode_std_hours("CES"), 0.5)
        self.assertEqual(hourly_push.get_pm_jcode_std_hours("BMP"), 0.5)

    def test_case_insensitive(self):
        self.assertEqual(hourly_push.get_pm_jcode_std_hours("cee"), 4.17)

    def test_unknown_returns_none(self):
        self.assertIsNone(hourly_push.get_pm_jcode_std_hours("XYZ"))

    def test_none_or_empty_returns_none(self):
        self.assertIsNone(hourly_push.get_pm_jcode_std_hours(None))
        self.assertIsNone(hourly_push.get_pm_jcode_std_hours(""))


class TestPmElapsedHours(unittest.TestCase):
    def test_computes_hours_between_in_time_and_now(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        self.assertAlmostEqual(hourly_push._pm_elapsed_hours("2026/08/09 15:30", now), 2.0)

    def test_invalid_format_returns_none(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        self.assertIsNone(hourly_push._pm_elapsed_hours("2026-08-09 15:30", now))

    def test_none_returns_none(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        self.assertIsNone(hourly_push._pm_elapsed_hours(None, now))


class TestPmDetailLines(unittest.TestCase):
    def test_formats_entity_status_elapsed_time_and_jcode(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        rows = [{"entity": "BA205", "status": "IN-REPAIR", "in_time": "2026/08/09 15:30", "jcode": "CEDO"}]
        lines = hourly_push._pm_detail_lines(rows, now)
        self.assertEqual(lines, ["BA205  修機中  2.00hr  CEDO"])

    def test_missing_jcode_omits_jcode_field(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        rows = [{"entity": "BA205", "status": "IN-REPAIR", "in_time": "2026/08/09 15:30", "jcode": None}]
        lines = hourly_push._pm_detail_lines(rows, now)
        self.assertEqual(lines, ["BA205  修機中  2.00hr"])

    def test_unparseable_in_time_shows_question_mark(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        rows = [{"entity": "BA205", "status": "SETUP", "in_time": None, "jcode": None}]
        lines = hourly_push._pm_detail_lines(rows, now)
        self.assertEqual(lines, ["BA205  改機中  ?"])

    def test_unknown_status_shown_as_is(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        rows = [{"entity": "BA205", "status": "WEIRD", "in_time": None, "jcode": None}]
        lines = hourly_push._pm_detail_lines(rows, now)
        self.assertEqual(lines, ["BA205  WEIRD  ?"])


class TestPmOvertimeLines(unittest.TestCase):
    def test_machine_over_standard_hours_included_with_operator(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        # CEE標準4.17hr，經過5hr超時
        rows = [{"entity": "BA205", "status": "SETUP", "in_time": "2026/08/09 12:30",
                  "jcode": "CEE", "operator": "E12345"}]
        lines = hourly_push._pm_overtime_lines(rows, now)
        self.assertEqual(len(lines), 1)
        self.assertIn("BA205", lines[0])
        self.assertIn("CEE", lines[0])
        self.assertIn("人員E12345", lines[0])

    def test_missing_operator_shows_placeholder(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        rows = [{"entity": "BA205", "status": "SETUP", "in_time": "2026/08/09 12:30",
                  "jcode": "CEE", "operator": None}]
        lines = hourly_push._pm_overtime_lines(rows, now)
        self.assertIn("人員未指定", lines[0])

    def test_machine_within_standard_hours_excluded(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        # CD標準0.5hr，只過了0.2hr，還沒超時
        rows = [{"entity": "BA205", "status": "SETUP", "in_time": "2026/08/09 17:18",
                  "jcode": "CD", "operator": "E1"}]
        lines = hourly_push._pm_overtime_lines(rows, now)
        self.assertEqual(lines, [])

    def test_unknown_jcode_excluded(self):
        now = datetime.datetime(2026, 8, 9, 17, 30)
        rows = [{"entity": "BA205", "status": "SETUP", "in_time": "2026/08/09 10:00",
                  "jcode": "UNKNOWN", "operator": "E1"}]
        lines = hourly_push._pm_overtime_lines(rows, now)
        self.assertEqual(lines, [])


class TestPmMonitorIntegrationInPushMessage(unittest.TestCase):
    """完整走build_hourly_push_message()，驗證機台明細+超時機台(帶機台號碼跟
    operator工號)都有出現在推播訊息裡。"""

    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_push_message_includes_machine_detail_and_overtime_with_operator(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        _add_pm_monitor_rows(hourly_push.DB_PATH, [
            {"entity": "BA205", "status": "SETUP", "jcode": "CEE", "operator": "E12345",
             "in_time": "2026/08/09 12:30"},
        ], fetched_at="2026-08-09T17:00:00")
        now = datetime.datetime(2026, 8, 9, 17, 30)
        msg = hourly_push.build_hourly_push_message(now=now)
        self.assertIn("機台明細:", msg)
        self.assertIn("BA205  改機中  5.00hr  CEE", msg)
        overtime_section = msg.split("⏰ 超時機台")[1]
        self.assertIn("BA205", overtime_section)
        self.assertIn("人員E12345", overtime_section)


if __name__ == "__main__":
    unittest.main()
