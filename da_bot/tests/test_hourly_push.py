"""hourly_push.py 的離線單元測試(不連網)：驗證今日改機統計(EE Maintenance完成數
+PM Monitor改機中/待改數+EPOXY依job_code分類)、即時機況機台明細、PM Monitor
JCODE超時判斷、以及官方GROUP分組稼動率統計邏輯。"""

import conftest  # noqa: F401  (設定 sys.path)

import datetime
import sqlite3
import tempfile
import unittest

import hourly_push

# 固定的測試時間點(下午2點，確定已經過了07:30早班交接時間)，讓「今日改機
# 統計」這類跟班別對齊的測試不受實際執行時間影響，結果穩定、不會因為剛好
# 在半夜00:00~07:30之間跑測試就失敗
_TEST_NOW = datetime.datetime(2026, 8, 9, 14, 0)

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
        # LOC就是CM700機型的官方GROUP分類名稱，BA8開頭全部都算LOC
        # (2026/08/09使用者確認，不用再拆成CM700跟LOC兩組)
        self.assertEqual(hourly_push._group_for_machine("BA801"), "LOC")
        self.assertEqual(hourly_push._group_for_machine("BA802"), "LOC")
        self.assertEqual(hourly_push._group_for_machine("BA893"), "LOC")

    def test_fc_prefixes(self):
        self.assertEqual(hourly_push._group_for_machine("BA512"), "FC")
        self.assertEqual(hourly_push._group_for_machine("BAD01"), "FC")

    def test_unknown_prefix_returns_none(self):
        self.assertIsNone(hourly_push._group_for_machine("XY999"))

    def test_empty_returns_none(self):
        self.assertIsNone(hourly_push._group_for_machine(""))
        self.assertIsNone(hourly_push._group_for_machine(None))


class TestShiftDayBounds(unittest.TestCase):
    """「今日改機統計」的「今日」要跟班別對齊(夜班19:30~07:30/早班07:30~19:30，
    2026/08/09使用者確認)，不是日曆日(午夜00:00分界)。"""

    def test_after_shift_change_returns_same_calendar_day(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)  # 下午2點，早班中
        shift_date, next_date = hourly_push._shift_day_bounds(now)
        self.assertEqual(shift_date, "2026-08-09")
        self.assertEqual(next_date, "2026-08-10")

    def test_exactly_at_shift_change_counts_as_new_day(self):
        now = datetime.datetime(2026, 8, 9, 7, 30)  # 剛好07:30，早班開始
        shift_date, next_date = hourly_push._shift_day_bounds(now)
        self.assertEqual(shift_date, "2026-08-09")

    def test_before_shift_change_still_counts_as_previous_day(self):
        # 凌晨03:00還在昨晚的夜班裡，還沒進入今天的班別日
        now = datetime.datetime(2026, 8, 9, 3, 0)
        shift_date, next_date = hourly_push._shift_day_bounds(now)
        self.assertEqual(shift_date, "2026-08-08")
        self.assertEqual(next_date, "2026-08-09")

    def test_one_minute_before_shift_change(self):
        now = datetime.datetime(2026, 8, 9, 7, 29)
        shift_date, _ = hourly_push._shift_day_bounds(now)
        self.assertEqual(shift_date, "2026-08-08")


class TestChangeoverJcodeCategory(unittest.TestCase):
    """真正改機job_code的判斷標準依機型群組各自不同(2026/08/09使用者分兩次
    提供)：ESEC/DB(EPOXY，Die Attach設備)是CED/CEE/CD前綴比對+"CE"獨立
    精確比對("CE"比"CED"/"CEE"短，不是誰的前綴)；LOC(CM700設備，機型完全
    不同)是CN/CD前綴比對，沒有"CE"這個獨立代碼。"""

    def test_esec_and_db_ced_prefix_family(self):
        for group in ("ESEC", "DB"):
            for jc in ("CED", "CEDO", "CED-1", "CED-M2"):
                self.assertEqual(hourly_push._changeover_jcode_category(group, jc), "CED機台", msg=(group, jc))

    def test_esec_and_db_cee_prefix_family(self):
        for group in ("ESEC", "DB"):
            for jc in ("CEE", "CEEO", "CEE123"):
                self.assertEqual(hourly_push._changeover_jcode_category(group, jc), "CEE機台", msg=(group, jc))

    def test_esec_and_db_cd_prefix_family(self):
        for group in ("ESEC", "DB"):
            for jc in ("CD", "CD-2"):
                self.assertEqual(hourly_push._changeover_jcode_category(group, jc), "CD機台", msg=(group, jc))

    def test_esec_and_db_ce_exact_match_maps_to_cee(self):
        for group in ("ESEC", "DB"):
            self.assertEqual(hourly_push._changeover_jcode_category(group, "CE"), "CEE機台", msg=group)
            self.assertEqual(hourly_push._changeover_jcode_category(group, "ce"), "CEE機台", msg=group)

    def test_esec_and_db_non_changeover_codes_return_none(self):
        for group in ("ESEC", "DB"):
            for jc in ("INK", "AING", "CWT", "OC", "CWTM", "EI", "XI", None, ""):
                self.assertIsNone(hourly_push._changeover_jcode_category(group, jc), msg=(group, jc))

    def test_loc_cn_prefix_family(self):
        # LOC(CM700設備)的真正改機代碼是CN/CD家族，CNO被CN前綴涵蓋，
        # 2026/08/09使用者確認
        for jc in ("CN", "CNO", "CN-1"):
            self.assertEqual(hourly_push._changeover_jcode_category("LOC", jc), "CN機台", msg=jc)

    def test_loc_cd_prefix_family(self):
        for jc in ("CD", "CD-2"):
            self.assertEqual(hourly_push._changeover_jcode_category("LOC", jc), "CD機台", msg=jc)

    def test_loc_does_not_recognize_ced_cee_or_ce(self):
        # LOC不是Die Attach設備，不該套用ESEC/DB那套CED/CEE/CE標準
        for jc in ("CED", "CEE", "CE", "CEDO"):
            self.assertIsNone(hourly_push._changeover_jcode_category("LOC", jc), msg=jc)

    def test_fc_falls_back_to_epoxy_family_by_default(self):
        # FlipChip還沒跟使用者確認過標準，暫時沿用ESEC/DB同一套當預設
        self.assertEqual(hourly_push._changeover_jcode_category("FC", "CED"), "CED機台")

    def test_unknown_group_returns_none(self):
        self.assertIsNone(hourly_push._changeover_jcode_category("UNKNOWN", "CED"))


class TestGetEpoxyDoneByJcode(unittest.TestCase):
    """EPOXY(ESEC+DB)今日已完成的改機次數，依「實際job_code」逐一列出台數。"""

    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_groups_by_actual_jcode_not_broad_category(self):
        # 2026/08/09使用者要求要看到CED/CEDO/CD這些「實際代碼」各自的台數，
        # 不要收斂成CED機台/CEE機台/CD機台三個大類，把CEDO藏在CED機台裡看不到
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "CED"},    # ESEC
            {"machine_id": "BA401", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "CEDO"},   # ESEC
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "CEDO"},   # DB
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "CD"},     # DB
            {"machine_id": "BA801", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "CED"},    # LOC, 不算EPOXY
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "INK"},    # ESEC，但INK不是真正改機，不算
        ])
        result = hourly_push.get_epoxy_done_by_jcode(_TEST_NOW)
        self.assertEqual(result, {"CED": 1, "CEDO": 2, "CD": 1})
        self.assertEqual(sum(result.values()), 4)

    def test_non_changeover_jcode_not_counted(self):
        # CPIS的e_tag='S'不是每一筆都是機型改機，補墨水(INK)/AI視覺校正(AING)/
        # 換料(CWT)/操作員備註(OC)這類生產中的小動作也會被標成'S'，
        # 2026/08/09使用者確認這些不算改機，一律不計入(也不放進"其他"桶)
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "INK"},
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "AING"},
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "CWT"},
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "OC"},
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(_TEST_NOW), {})

    def test_non_epoxy_group_excluded(self):
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA801", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "CED"},  # LOC
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(_TEST_NOW), {})

    def test_e_tag_r_excluded(self):
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "R", "end_date": today, "end_time": "10:00", "job_code": "CED"},
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(_TEST_NOW), {})

    def test_not_completed_today_excluded(self):
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": "2026-08-01", "end_time": "10:00", "job_code": "CED"},
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(_TEST_NOW), {})

    def test_completed_just_after_midnight_still_belongs_to_previous_shift_day(self):
        # 08/09凌晨02:00完成的改機，還算在08/08的班別日裡(夜班橫跨午夜)。
        # 如果現在是08/09下午(已經進入08/09的班別日)，這筆08/08班別日的紀錄
        # 已經「過去了」，不該再被算進「今日」改機統計
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": "2026-08-09", "end_time": "02:00", "job_code": "CED"},
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(_TEST_NOW), {})

    def test_completed_just_after_midnight_counted_while_still_in_that_shift_day(self):
        # 同一筆08/09凌晨02:00完成的紀錄，如果現在還是08/09凌晨03:00(還在
        # 08/08那個班別日裡，還沒到07:30交班)，就應該要算進「今日」
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": "2026-08-09", "end_time": "02:00", "job_code": "CED"},
        ])
        now = datetime.datetime(2026, 8, 9, 3, 0)
        self.assertEqual(hourly_push.get_epoxy_done_by_jcode(now), {"CED": 1})


class TestGetEpoxyDoneByShift(unittest.TestCase):
    """EPOXY(ESEC+DB)今日已完成改機依早班(07:30~19:30)/夜班(19:30~次日07:30)
    分類(2026/08/09使用者要求)，跟get_epoxy_done_by_jcode()同一套CED/CEE/CD
    篩選標準。"""

    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_splits_by_day_and_night_shift(self):
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "08:00", "job_code": "CED"},   # 早班
            {"machine_id": "BA401", "e_tag": "S", "end_date": today, "end_time": "19:00", "job_code": "CED"},   # 早班(19:30前)
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "20:00", "job_code": "CEDO"},  # 夜班
            {"machine_id": "BAA02", "e_tag": "S", "end_date": "2026-08-10", "end_time": "02:00", "job_code": "CD"},  # 跨午夜的夜班
        ])
        now = datetime.datetime(2026, 8, 9, 14, 0)
        result = hourly_push.get_epoxy_done_by_shift(now)
        self.assertEqual(result, {"早班": 2, "夜班": 2})

    def test_non_changeover_jcode_excluded_from_shift_counts(self):
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "08:00", "job_code": "INK"},
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_shift(_TEST_NOW), {"早班": 0, "夜班": 0})

    def test_non_epoxy_group_excluded_from_shift_counts(self):
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA801", "e_tag": "S", "end_date": today, "end_time": "08:00", "job_code": "CED"},  # LOC
        ])
        self.assertEqual(hourly_push.get_epoxy_done_by_shift(_TEST_NOW), {"早班": 0, "夜班": 0})

    def test_total_matches_get_epoxy_done_by_jcode_total(self):
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "08:00", "job_code": "CED"},
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "20:00", "job_code": "CEDO"},
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "end_time": "21:00", "job_code": "CD"},
        ])
        by_jcode = hourly_push.get_epoxy_done_by_jcode(_TEST_NOW)
        by_shift = hourly_push.get_epoxy_done_by_shift(_TEST_NOW)
        self.assertEqual(sum(by_jcode.values()), sum(by_shift.values()))


class TestGetSetupGroupStats(unittest.TestCase):
    """今日改機統計：done(今日完成)算自ee_maintenance_record；in_progress(改機中)/
    waiting(待改)改成算自pm_monitor_record的即時快照(SETUP/WAIT-SETUP)。"""

    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_done_from_ee_maintenance_in_progress_and_waiting_from_pm_monitor(self):
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "bgn_date": "2026-08-08", "bgn_time": "10:00",
             "end_date": today, "end_time": "12:00", "job_code": "CED"},   # ESEC, 今日完成
            # e_tag=R的紀錄不該被算進改機統計
            {"machine_id": "BA512", "e_tag": "R", "bgn_date": "2026-08-09", "bgn_time": "10:00",
             "job_code": "CE"},
        ])
        _add_pm_monitor_rows(hourly_push.DB_PATH, [
            {"entity": "BAA01", "status": "SETUP", "jcode": "CED"},      # DB, 改機中
            {"entity": "BA801", "status": "WAIT-SETUP", "jcode": "CN"},  # LOC(CN/CD家族), 待改
        ])
        stats = hourly_push.get_setup_group_stats(_TEST_NOW)
        self.assertEqual(stats["ESEC"], {"done": 1, "in_progress": 0, "waiting": 0})
        self.assertEqual(stats["DB"], {"done": 0, "in_progress": 1, "waiting": 0})
        self.assertEqual(stats["LOC"], {"done": 0, "in_progress": 0, "waiting": 1})
        self.assertEqual(stats["FC"], {"done": 0, "in_progress": 0, "waiting": 0})

    def test_completed_on_a_different_day_not_counted_as_done_today(self):
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "bgn_date": "2026-08-01", "bgn_time": "10:00",
             "end_date": "2026-08-01", "end_time": "12:00", "job_code": "CED"},
        ])
        stats = hourly_push.get_setup_group_stats(_TEST_NOW)
        self.assertEqual(stats["ESEC"]["done"], 0)

    def test_non_changeover_jcode_not_counted_as_done(self):
        # e_tag='S'裡INK(補墨水)/AING(AI視覺校正)這類生產中的小動作不算改機，
        # 2026/08/09使用者確認只算CED/CEE/CD三類，其餘一律不計入done
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "INK"},
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "10:00", "job_code": "AING"},
        ])
        stats = hourly_push.get_setup_group_stats(_TEST_NOW)
        self.assertEqual(stats["ESEC"]["done"], 0)

    def test_no_pm_monitor_table_leaves_in_progress_and_waiting_zero(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        stats = hourly_push.get_setup_group_stats(_TEST_NOW)
        for g in ("ESEC", "DB", "LOC", "FC"):
            self.assertEqual(stats[g], {"done": 0, "in_progress": 0, "waiting": 0})


class TestBuildHourlyPushMessageNewSections(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = hourly_push.DB_PATH

    def tearDown(self):
        hourly_push.DB_PATH = self._orig_db_path

    def test_includes_setup_stats_and_epoxy_jcode_breakdown(self):
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "bgn_date": "2026-08-08", "bgn_time": "10:00",
             "end_date": today, "end_time": "12:00", "job_code": "CED"},
            {"machine_id": "BAA01", "e_tag": "S", "bgn_date": "2026-08-08", "bgn_time": "10:00",
             "end_date": today, "end_time": "12:00", "job_code": "CEDO"},
        ])
        msg = hourly_push.build_hourly_push_message(now=_TEST_NOW)
        self.assertIn("🔧 今日改機統計", msg)
        self.assertIn("EPOXY   改機2 | 改機中0 | 待改0", msg)
        # 逐一列出實際job_code(不再收斂成CED機台這種大類)，加總要等於改機總數
        self.assertIn("CED1台", msg)
        self.assertIn("CEDO1台", msg)

    def test_includes_shift_breakdown(self):
        # 2026/08/09使用者要求要在推播裡補上早班/夜班改機台數
        today = _TEST_NOW.date().isoformat()
        hourly_push.DB_PATH = _make_db_with_records([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "08:00", "job_code": "CED"},   # 早班
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "20:00", "job_code": "CEDO"},  # 夜班
        ])
        msg = hourly_push.build_hourly_push_message(now=_TEST_NOW)
        self.assertIn("早班1台 夜班1台", msg)

    def test_overtime_section_shows_placeholder_when_no_pm_monitor_data(self):
        hourly_push.DB_PATH = _make_db_with_records([])
        msg = hourly_push.build_hourly_push_message(now=_TEST_NOW)
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
            {"entity": "BA205", "status": "IN-REPAIR"},                # ESEC
            {"entity": "BAA01", "status": "SETUP", "jcode": "CED"},     # DB
            {"entity": "BAA02", "status": "SETUP", "jcode": "CEDO"},    # DB
            {"entity": "BA801", "status": "WAIT-SETUP", "jcode": "CD"},  # LOC
        ])
        stats = hourly_push.get_pm_monitor_group_stats()
        self.assertEqual(stats["ESEC"], {"IN-REPAIR": 1})
        self.assertEqual(stats["DB"], {"SETUP": 2})
        self.assertEqual(stats["LOC"], {"WAIT-SETUP": 1})
        self.assertEqual(stats["FC"], {})

    def test_setup_status_excludes_non_changeover_jcode(self):
        # PM Monitor的STATUS='SETUP'底下混了CWTM/EI/INK這類生產中小動作，
        # 不是每一筆都是真正改機，2026/08/09使用者確認要跟改機完成的判斷
        # 標準統一，只算CED/CEE/CD/CE類的jcode
        hourly_push.DB_PATH = _make_db_with_records([])
        _add_pm_monitor_rows(hourly_push.DB_PATH, [
            {"entity": "BAA01", "status": "SETUP", "jcode": "CED"},   # 真正改機，算
            {"entity": "BAA02", "status": "SETUP", "jcode": "CWTM"},  # 生產中小動作，不算
            {"entity": "BAA03", "status": "SETUP", "jcode": "EI"},    # 生產中小動作，不算
            {"entity": "BAB01", "status": "WAIT-SETUP", "jcode": "INK"},  # 生產中小動作，不算
            {"entity": "BAB02", "status": "WAIT-SETUP", "jcode": "CE"},   # "CE"是獨立的真正改機代碼，算
        ])
        stats = hourly_push.get_pm_monitor_group_stats()
        self.assertEqual(stats["DB"], {"SETUP": 1, "WAIT-SETUP": 1})

    def test_non_changeover_statuses_not_filtered_by_jcode(self):
        # IN-REPAIR/WAIT-REPAIR/PM/ENG這些狀態不受jcode篩選影響，全部照算
        hourly_push.DB_PATH = _make_db_with_records([])
        _add_pm_monitor_rows(hourly_push.DB_PATH, [
            {"entity": "BAA01", "status": "IN-REPAIR", "jcode": "E"},
            {"entity": "BAA02", "status": "WAIT-REPAIR", "jcode": None},
            {"entity": "BAA03", "status": "PM", "jcode": "PE"},
            {"entity": "BAA04", "status": "ENG", "jcode": "PE"},
        ])
        stats = hourly_push.get_pm_monitor_group_stats()
        self.assertEqual(
            stats["DB"],
            {"IN-REPAIR": 1, "WAIT-REPAIR": 1, "PM": 1, "ENG": 1},
        )

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
