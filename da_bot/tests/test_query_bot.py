"""query_bot.py 的離線單元測試(不連網)：用暫存SQLite驗證full_info_reply()把
即時狀態/統計摘要/稼動率/設備健康正確組在一起(健康監控資料表不存在時要跳過，
不要噴錯)。"""

import conftest  # noqa: F401  (設定 sys.path)

import datetime
import json
import sqlite3
import tempfile
import unittest

import query_bot
import hourly_push
import engineer_master


def _make_engineer_master(entries):
    """entries是list of (工號, 姓名, 部門原始值OP/EE/PE)，寫成engineer_master.json
    格式的暫存檔，供工號+姓名、MFG/EE分類相關測試使用。"""
    path = tempfile.mktemp(suffix=".json")
    data = {key: {"name": name, "dept": dept} for key, name, dept in entries}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def _make_db(include_health_table=False):
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            machine_id TEXT, bgn_date TEXT, bgn_time TEXT, end_date TEXT, end_time TEXT,
            job_code TEXT, e_tag TEXT, engineer_id TEXT, cause TEXT, description TEXT,
            dur REAL, wait_dur REAL
        )
    """)
    conn.execute(
        "INSERT INTO ee_maintenance_record VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("BA205", "2026-08-01", "07:05", None, None, "CED", "R", "ENG1", "cause text", "desc text", None, None),
    )
    conn.execute("CREATE TABLE utilization_record (UTIL TEXT, SETUP TEXT, ENTITY TEXT, fetched_at TEXT)")
    conn.execute(
        "INSERT INTO utilization_record VALUES (?,?,?,?)",
        ("81.4 %", "3.4 %", "BA205", "2026-08-09T12:00:00"),
    )
    if include_health_table:
        conn.execute("""
            CREATE TABLE health_monitor_record (
                machine_id TEXT, model TEXT, health_score INTEGER, live_status TEXT,
                duration TEXT, eta TEXT, overdue TEXT, wip TEXT, jcode TEXT, handler TEXT,
                fetched_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO health_monitor_record VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("BA205", "2100SD", 80, "正常", "1hr", "", "", "", "", "", "2026-08-09T12:00:00"),
        )
    conn.commit()
    conn.close()
    return path


class TestFullInfoReply(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path

    def test_combines_live_summary_and_utilization(self):
        query_bot.DB_PATH = _make_db(include_health_table=False)
        reply = query_bot.full_info_reply("BA205")
        self.assertIn("進行中", reply)          # live_status_reply
        self.assertIn("修機", reply)             # summary_reply
        self.assertIn("稼動: 81.4 %", reply)  # utilization_reply
        # 沒有health_monitor_record表時要優雅跳過，不要把錯誤訊息混進回覆裡
        self.assertNotIn("資料表還不存在", reply)

    def test_includes_health_when_available(self):
        query_bot.DB_PATH = _make_db(include_health_table=True)
        reply = query_bot.full_info_reply("BA205")
        self.assertIn("健康分:80", reply)


def _make_db_with_pm_monitor(pm_row=None, no_pm_table=False):
    """pm_row是dict或None(該機台不在PM Monitor異常清單裡)。no_pm_table=True
    模擬pm_monitor_record資料表還沒建立(cpis_pm_monitor_scraper.py沒跑過)。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            machine_id TEXT, bgn_date TEXT, bgn_time TEXT, end_date TEXT, end_time TEXT,
            job_code TEXT, e_tag TEXT, engineer_id TEXT, cause TEXT, description TEXT,
            dur REAL, wait_dur REAL
        )
    """)
    conn.execute(
        "INSERT INTO ee_maintenance_record VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("BA205", "2026-08-01", "07:05", None, None, "CED", "R", "ENG1", "cause text", "desc text", None, None),
    )
    if not no_pm_table:
        conn.execute("""
            CREATE TABLE pm_monitor_record (
                oper TEXT, entity TEXT, model TEXT, status TEXT,
                lot_no TEXT, bond_id TEXT, wip TEXT, in_time TEXT,
                outplan TEXT, jcode TEXT, operator TEXT, fetched_at TEXT
            )
        """)
        if pm_row is not None:
            conn.execute(
                "INSERT INTO pm_monitor_record VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "DA", pm_row.get("entity", "BA205"), "DIE-ATTACH", pm_row.get("status", "IN-REPAIR"),
                    "", "", "0", pm_row.get("in_time", ""), "",
                    pm_row.get("jcode", "E"), pm_row.get("operator", "23535"),
                    "2026-08-09T17:00:00",
                ),
            )
    conn.commit()
    conn.close()
    return path


class TestLiveStatusReplyPrefersPmMonitor(unittest.TestCase):
    """live_status_reply()優先用PM/REPAIR/SETUP Monitor的即時快照，查不到
    該機台(不在異常清單裡，或資料表還沒抓過)才退回用EE Maintenance歷史
    紀錄推論。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path

    def test_uses_pm_monitor_status_when_available(self):
        query_bot.DB_PATH = _make_db_with_pm_monitor(
            {"status": "IN-REPAIR", "jcode": "E", "operator": "23535", "in_time": "2026/08/01 07:05"}
        )
        reply = query_bot.live_status_reply("BA205")
        self.assertIn("目前狀態(即時): 修機中", reply)
        self.assertIn("人員: 23535", reply)

    def test_falls_back_to_ee_maintenance_when_machine_not_in_pm_snapshot(self):
        # pm_monitor_record表存在，但這台機器不在裡面(代表正常運作中)
        query_bot.DB_PATH = _make_db_with_pm_monitor(pm_row=None)
        reply = query_bot.live_status_reply("BA205")
        self.assertIn("進行中", reply)  # 退回舊的EE Maintenance推論邏輯
        self.assertNotIn("(即時)", reply)

    def test_falls_back_to_ee_maintenance_when_pm_table_missing(self):
        # cpis_pm_monitor_scraper.py還沒跑過，pm_monitor_record表根本不存在
        query_bot.DB_PATH = _make_db_with_pm_monitor(no_pm_table=True)
        reply = query_bot.live_status_reply("BA205")
        self.assertIn("進行中", reply)
        self.assertNotIn("(即時)", reply)

    def test_pm_status_zh_labels(self):
        for code, zh in [("WAIT-SETUP", "等待改機"), ("SETUP", "改機中"), ("PM", "保養中")]:
            query_bot.DB_PATH = _make_db_with_pm_monitor({"status": code})
            reply = query_bot.live_status_reply("BA205")
            self.assertIn(zh, reply, msg=code)


def _make_db_for_group_tests(util_rows=None, pm_rows=None, ee_rows=None, no_pm_table=False):
    """util_rows: list of (model, entity)寫進utilization_record同一批fetched_at。
    pm_rows: list寫進pm_monitor_record同一批fetched_at，每個元素可以是
             (entity, status)這種簡單tuple(其餘欄位補None)，也可以是dict
             (例如{"entity": "BAA03", "status": "IN-REPAIR", "in_time": "...",
             "operator": "E"})，測試修機超時清單要用到in_time/operator時用dict；
             no_pm_table=True時完全不建pm_monitor_record表(模擬還沒抓過)。
    ee_rows: list of dict寫進ee_maintenance_record(缺欄位當NULL)。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            machine_id TEXT, bgn_date TEXT, bgn_time TEXT, end_date TEXT, end_time TEXT,
            job_code TEXT, e_tag TEXT, engineer_id TEXT, cause TEXT, description TEXT,
            dur REAL, wait_dur REAL
        )
    """)
    for row in (ee_rows or []):
        conn.execute(
            "INSERT INTO ee_maintenance_record VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (row.get("machine_id"), row.get("bgn_date"), row.get("bgn_time"), row.get("end_date"),
             row.get("end_time"), row.get("job_code"), row.get("e_tag"), row.get("engineer_id"),
             row.get("cause"), row.get("description"), row.get("dur"), row.get("wait_dur")),
        )
    conn.execute('CREATE TABLE utilization_record (MODEL TEXT, ENTITY TEXT, UTIL TEXT, fetched_at TEXT)')
    util_fetched_at = "2026-08-09T12:00:00"
    for model, entity in (util_rows or []):
        conn.execute(
            "INSERT INTO utilization_record (MODEL, ENTITY, fetched_at) VALUES (?,?,?)",
            (model, entity, util_fetched_at),
        )
    if not no_pm_table:
        conn.execute("""
            CREATE TABLE pm_monitor_record (
                oper TEXT, entity TEXT, model TEXT, status TEXT,
                lot_no TEXT, bond_id TEXT, wip TEXT, in_time TEXT,
                outplan TEXT, jcode TEXT, operator TEXT, fetched_at TEXT
            )
        """)
        pm_fetched_at = "2026-08-09T17:00:00"
        for pm_row in (pm_rows or []):
            if isinstance(pm_row, dict):
                entity, status = pm_row.get("entity"), pm_row.get("status")
                jcode, operator, in_time = pm_row.get("jcode"), pm_row.get("operator"), pm_row.get("in_time")
            else:
                entity, status = pm_row
                jcode, operator, in_time = None, None, None
            conn.execute(
                "INSERT INTO pm_monitor_record (entity, status, jcode, operator, in_time, fetched_at) "
                "VALUES (?,?,?,?,?,?)",
                (entity, status, jcode, operator, in_time, pm_fetched_at),
            )
    conn.commit()
    conn.close()
    return path


class TestGroupMachineIds(unittest.TestCase):
    """DB700/DB800/DB830的「共X台」改成查utilization_record的真實ENTITY清單，
    不再用MODEL_GROUPS裡手動維護、容易跟實際機台增減脫節的固定範圍清單。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path

    def test_uses_real_entities_from_utilization_record(self):
        query_bot.DB_PATH = _make_db_for_group_tests(util_rows=[
            ("DB830", "BAB01"), ("DB830", "BAB02"), ("DB700", "BA701"),
        ])
        conn = query_bot.get_conn()
        cur = conn.cursor()
        ids = query_bot._group_machine_ids(cur, "DB830")
        conn.close()
        self.assertEqual(sorted(ids), ["BAB01", "BAB02"])

    def test_epoxy_db_combines_db700_db800_db830(self):
        query_bot.DB_PATH = _make_db_for_group_tests(util_rows=[
            ("DB700", "BA701"), ("DB800", "BAA01"), ("DB830", "BAB01"),
        ])
        conn = query_bot.get_conn()
        cur = conn.cursor()
        ids = query_bot._group_machine_ids(cur, "EPOXY(DB)")
        conn.close()
        self.assertEqual(sorted(ids), ["BA701", "BAA01", "BAB01"])

    def test_falls_back_to_static_list_when_no_utilization_data(self):
        query_bot.DB_PATH = _make_db_for_group_tests(util_rows=[])
        conn = query_bot.get_conn()
        cur = conn.cursor()
        ids = query_bot._group_machine_ids(cur, "DB830")
        conn.close()
        self.assertEqual(ids, query_bot.MODEL_GROUPS["DB830"])

    def test_static_group_esec2100_unaffected(self):
        query_bot.DB_PATH = _make_db_for_group_tests(util_rows=[("DB830", "BAB01")])
        conn = query_bot.get_conn()
        cur = conn.cursor()
        ids = query_bot._group_machine_ids(cur, "Esec2100")
        conn.close()
        self.assertEqual(ids, query_bot.MODEL_GROUPS["Esec2100"])


class TestDbGroupReplyUsesPmMonitorForStatus(unittest.TestCase):
    """db_group_reply()的「共X台‧修機X‧改機X‧正常X」：機台清單改用真實
    utilization_record清單；修機中/改機中的判斷優先用PM Monitor即時快照，
    不再靠EE Maintenance歷史紀錄推論(2026/08/09使用者回報「DB 改機140台」
    這種數字不準，正確的話應該是個位數)。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path

    def test_machine_count_reflects_real_utilization_record_list(self):
        query_bot.DB_PATH = _make_db_for_group_tests(util_rows=[
            ("DB830", "BAB01"), ("DB830", "BAB02"),
        ])
        reply = query_bot.db_group_reply(["DB830"])
        self.assertIn("共2台", reply)

    def test_repair_and_setup_counts_come_from_pm_monitor(self):
        query_bot.DB_PATH = _make_db_for_group_tests(
            util_rows=[("DB830", "BAB01"), ("DB830", "BAB02"), ("DB830", "BAB03")],
            pm_rows=[("BAB01", "IN-REPAIR"), ("BAB02", "SETUP")],
        )
        reply = query_bot.db_group_reply(["DB830"])
        self.assertIn("共3台 · 修機1 · 改機1 · 正常1", reply)

    def test_stale_ee_maintenance_record_ignored_once_pm_monitor_has_data(self):
        # EE Maintenance裡BAB01有一筆沒關閉的修機紀錄(e_tag=R、end_time是空的)，
        # 但PM Monitor(當下真正的機況)已經抓過資料、而且BAB01不在異常清單裡，
        # 代表已經恢復正常了，不該再被EE Maintenance的舊紀錄誤判成修機中
        query_bot.DB_PATH = _make_db_for_group_tests(
            util_rows=[("DB830", "BAB01")],
            pm_rows=[("BAB02", "IN-REPAIR")],  # PM Monitor有抓到資料，只是BAB01不在裡面
            ee_rows=[{"machine_id": "BAB01", "bgn_date": "2026-08-09", "bgn_time": "07:00",
                      "e_tag": "R"}],
        )
        reply = query_bot.db_group_reply(["DB830"])
        self.assertIn("共1台 · 修機0 · 改機0 · 正常1", reply)

    def test_falls_back_to_ee_maintenance_when_pm_monitor_table_missing(self):
        query_bot.DB_PATH = _make_db_for_group_tests(
            util_rows=[("DB830", "BAB01")],
            ee_rows=[{"machine_id": "BAB01", "bgn_date": "2026-08-09", "bgn_time": "07:00",
                      "e_tag": "R"}],
            no_pm_table=True,
        )
        reply = query_bot.db_group_reply(["DB830"])
        self.assertIn("共1台 · 修機1 · 改機0 · 正常0", reply)


class TestDbGroupReplyOvertimeRepairList(unittest.TestCase):
    """異常機台清單改成只顯示「修機中且已經超過1小時」的機台，並附上修機
    超時多久跟修機人員(2026/08/10使用者要求)，不再把改機中/工程異常/等待
    修機這些狀態全部混在一起列出來洗版。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH
        self._orig_master_path = engineer_master.PATH
        self._orig_master_cache = engineer_master._cache
        engineer_master.PATH = "/tmp/does_not_exist_engineer_master_test.json"
        engineer_master._cache = None

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path
        engineer_master.PATH = self._orig_master_path
        engineer_master._cache = self._orig_master_cache

    @staticmethod
    def _in_time_hours_ago(hours):
        dt = datetime.datetime.now() - datetime.timedelta(hours=hours)
        return dt.strftime("%Y/%m/%d %H:%M")

    def test_lists_repair_machine_over_one_hour_with_elapsed_and_operator(self):
        query_bot.DB_PATH = _make_db_for_group_tests(
            util_rows=[("DB830", "BAB01")],
            pm_rows=[{"entity": "BAB01", "status": "IN-REPAIR",
                      "in_time": self._in_time_hours_ago(2), "operator": "E1"}],
        )
        reply = query_bot.db_group_reply(["DB830"])
        self.assertIn("異常機台(修機超時1hr以上)", reply)
        self.assertIn("BAB01 修機超時2.0", reply)
        self.assertIn("/E1", reply)

    def test_excludes_repair_machine_under_one_hour(self):
        query_bot.DB_PATH = _make_db_for_group_tests(
            util_rows=[("DB830", "BAB01")],
            pm_rows=[{"entity": "BAB01", "status": "IN-REPAIR",
                      "in_time": self._in_time_hours_ago(0.5), "operator": "E1"}],
        )
        reply = query_bot.db_group_reply(["DB830"])
        self.assertNotIn("異常機台", reply)

    def test_excludes_non_repair_status_even_when_over_one_hour(self):
        query_bot.DB_PATH = _make_db_for_group_tests(
            util_rows=[("DB830", "BAB01")],
            pm_rows=[{"entity": "BAB01", "status": "SETUP",
                      "in_time": self._in_time_hours_ago(3), "operator": "E1"}],
        )
        reply = query_bot.db_group_reply(["DB830"])
        self.assertNotIn("異常機台", reply)

    def test_no_overtime_list_when_pm_monitor_has_no_data(self):
        query_bot.DB_PATH = _make_db_for_group_tests(
            util_rows=[("DB830", "BAB01")],
            ee_rows=[{"machine_id": "BAB01", "bgn_date": "2026-08-09", "bgn_time": "07:00",
                      "e_tag": "R"}],
            no_pm_table=True,
        )
        reply = query_bot.db_group_reply(["DB830"])
        self.assertNotIn("異常機台", reply)

    def test_operator_name_appended_when_found(self):
        # 2026/08/10使用者要求：工號後面要補上姓名
        query_bot.DB_PATH = _make_db_for_group_tests(
            util_rows=[("DB830", "BAB01")],
            pm_rows=[{"entity": "BAB01", "status": "IN-REPAIR",
                      "in_time": self._in_time_hours_ago(2), "operator": "s10435"}],
        )
        engineer_master.PATH = _make_engineer_master([("10435", "王小明", "EE")])
        engineer_master._cache = None
        reply = query_bot.db_group_reply(["DB830"])
        self.assertIn("/s10435(王小明)", reply)


class TestAllLiveStatusReply(unittest.TestCase):
    """all_live_status_reply()：不指定機台代號的「即時機況查詢」(2026/08/10
    使用者要求)，全公司PM Monitor異常機況總覽——分組台數、機台明細、超時
    機台，直接複用hourly_push整點推播「⚡即時機況」段落同一套邏輯。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH
        self._orig_hp_db_path = hourly_push.DB_PATH

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path
        hourly_push.DB_PATH = self._orig_hp_db_path

    def _use_db(self, **kwargs):
        path = _make_db_for_group_tests(**kwargs)
        query_bot.DB_PATH = path
        hourly_push.DB_PATH = path

    def test_no_data_message_when_pm_monitor_table_missing(self):
        self._use_db(no_pm_table=True)
        reply = query_bot.all_live_status_reply()
        self.assertIn("目前無PM Monitor即時機況資料", reply)

    def test_lists_group_stats_machine_detail_and_overtime(self):
        self._use_db(pm_rows=[
            {"entity": "BAA01", "status": "IN-REPAIR",
             "in_time": "2026/08/09 10:00", "operator": "s10435"},
            {"entity": "BA801", "status": "SETUP",
             "in_time": "2026/08/09 10:00", "jcode": "CN"},
        ])
        now = datetime.datetime(2026, 8, 9, 16, 0)
        reply = query_bot.all_live_status_reply(now=now)

        self.assertIn("【即時機況】", reply)
        # BAA01(DB)修機中、BA801(LOC)改機中，各組分組台數都要出現
        self.assertIn("修機中1", reply)
        self.assertIn("改機中1", reply)
        # 機台明細列出機台號碼+狀態+已耗時(6小時)，BA801還要附JCODE
        self.assertIn("機台明細:", reply)
        self.assertIn("BAA01  修機中  6.00hr", reply)
        self.assertIn("BA801  改機中  6.00hr  CN", reply)
        # BA801(CN標準工時3.38hr)已超時，BAA01沒有jcode所以無法判斷標準工時、不列入超時
        self.assertIn("超時機台:", reply)
        self.assertIn("BA801", reply.split("超時機台:")[1])
        self.assertNotIn("BAA01", reply.split("超時機台:")[1])

    def test_no_overtime_placeholder_when_nothing_over_standard(self):
        self._use_db(pm_rows=[
            {"entity": "BAA01", "status": "IN-REPAIR", "in_time": "2026/08/09 15:30"},
        ])
        now = datetime.datetime(2026, 8, 9, 16, 0)
        reply = query_bot.all_live_status_reply(now=now)
        self.assertIn("(目前無超過標準工時的機台)", reply)


def _make_db_for_official_downrate_tests(rows):
    """rows是list of dict，每個可含model/entity/util/fetched_at/
    query_date_start/query_date_end(缺的欄位當NULL)，寫進utilization_record。
    entity留空(None)代表這是官方GROUP彙總列(不是個別機台列)。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE utilization_record (
            MODEL TEXT, ENTITY TEXT, UTIL TEXT, "SETUP" TEXT, fetched_at TEXT,
            query_date_start TEXT, query_date_end TEXT
        )
    """)
    for row in rows:
        conn.execute(
            'INSERT INTO utilization_record (MODEL, ENTITY, UTIL, "SETUP", fetched_at, '
            "query_date_start, query_date_end) VALUES (?,?,?,?,?,?,?)",
            (row.get("model"), row.get("entity"), row.get("util"), row.get("setup"),
             row.get("fetched_at"), row.get("query_date_start"), row.get("query_date_end")),
        )
    conn.commit()
    conn.close()
    return path


class TestGroupOfficialDownrateReply(unittest.TestCase):
    """「<官方群組名稱>downrate」查詢：CPIS官方GROUP彙總表原始一列數字，
    可以加日期(例如"8/9 DB800downrate")指定查那一天抓到的資料
    (2026/08/10使用者要求)。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path

    def test_returns_latest_row_when_no_date_given(self):
        query_bot.DB_PATH = _make_db_for_official_downrate_tests([
            {"model": "DB800", "entity": None, "util": "80.0 %", "setup": "5.0 %",
             "fetched_at": "2026-08-08T12:00:00",
             "query_date_start": "20260808", "query_date_end": "20260808"},
            {"model": "DB800", "entity": None, "util": "82.0 %", "setup": "6.0 %",
             "fetched_at": "2026-08-09T12:00:00",
             "query_date_start": "20260809", "query_date_end": "20260809"},
        ])
        reply = query_bot.group_official_downrate_reply("DB800")
        self.assertIn("稼動(UTIL): 82.0 %", reply)

    def test_date_filters_to_that_days_latest_fetch(self):
        query_bot.DB_PATH = _make_db_for_official_downrate_tests([
            {"model": "DB800", "entity": None, "util": "80.0 %", "setup": "5.0 %",
             "fetched_at": "2026-08-08T09:00:00",
             "query_date_start": "20260808", "query_date_end": "20260808"},
            {"model": "DB800", "entity": None, "util": "84.0 %", "setup": "4.0 %",
             "fetched_at": "2026-08-08T20:00:00",
             "query_date_start": "20260808", "query_date_end": "20260808"},
            {"model": "DB800", "entity": None, "util": "82.0 %", "setup": "6.0 %",
             "fetched_at": "2026-08-09T12:00:00",
             "query_date_start": "20260809", "query_date_end": "20260809"},
        ])
        reply = query_bot.group_official_downrate_reply("DB800", date_ymd="20260808")
        self.assertIn("稼動(UTIL): 84.0 %", reply)

    def test_warns_when_last_fetch_of_the_day_looks_incomplete(self):
        # 2026/08/10使用者實測發現：DB700那天我們回82.1%，CPIS網頁事後查是
        # 80.3%，追查是我們存的「當天最後一筆」停在15:00(服務中途停過)。
        # 加這個警告提示使用者數字可能不是事後結算的最終版本
        query_bot.DB_PATH = _make_db_for_official_downrate_tests([
            {"model": "DB700", "entity": None, "util": "82.1 %",
             "fetched_at": "2026-08-09 15:00:26",
             "query_date_start": "20260809", "query_date_end": "20260809"},
        ])
        reply = query_bot.group_official_downrate_reply("DB700", date_ymd="20260809", date_label="08/09")
        self.assertIn("⚠️", reply)
        self.assertIn("cpis_utilization_scraper.py 20260809 20260809", reply)

    def test_no_warning_when_last_fetch_is_late_in_the_day(self):
        query_bot.DB_PATH = _make_db_for_official_downrate_tests([
            {"model": "DB700", "entity": None, "util": "80.3 %",
             "fetched_at": "2026-08-09 22:00:00",
             "query_date_start": "20260809", "query_date_end": "20260809"},
        ])
        reply = query_bot.group_official_downrate_reply("DB700", date_ymd="20260809", date_label="08/09")
        self.assertNotIn("⚠️", reply)

    def test_no_warning_when_date_not_specified(self):
        query_bot.DB_PATH = _make_db_for_official_downrate_tests([
            {"model": "DB700", "entity": None, "util": "82.1 %",
             "fetched_at": "2026-08-09 15:00:26",
             "query_date_start": "20260809", "query_date_end": "20260809"},
        ])
        reply = query_bot.group_official_downrate_reply("DB700")
        self.assertNotIn("⚠️", reply)

    def test_no_data_for_that_date_shows_date_specific_message(self):
        query_bot.DB_PATH = _make_db_for_official_downrate_tests([
            {"model": "DB800", "entity": None, "util": "82.0 %",
             "fetched_at": "2026-08-09T12:00:00",
             "query_date_start": "20260809", "query_date_end": "20260809"},
        ])
        reply = query_bot.group_official_downrate_reply("DB800", date_ymd="20260808", date_label="08/08")
        self.assertIn("DB800 08/08查無官方GROUP彙總資料", reply)

    def test_entity_rows_excluded_from_group_summary(self):
        query_bot.DB_PATH = _make_db_for_official_downrate_tests([
            {"model": "DB800", "entity": "BAB01", "util": "70.0 %",
             "fetched_at": "2026-08-09T12:00:00",
             "query_date_start": "20260809", "query_date_end": "20260809"},
        ])
        reply = query_bot.group_official_downrate_reply("DB800")
        self.assertIn("查無官方GROUP彙總資料", reply)


class TestAllGroupsOfficialDownrateReply(unittest.TestCase):
    """「down rate」查詢(不指定官方群組時)：列出全部官方群組的downrate彙總，
    可以加日期(2026/08/10使用者要求)。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path

    def test_lists_all_official_groups(self):
        rows = [
            {"model": label, "entity": None, "util": "80.0 %",
             "fetched_at": "2026-08-09T12:00:00",
             "query_date_start": "20260809", "query_date_end": "20260809"}
            for label in query_bot.OFFICIAL_GROUP_LABELS
        ]
        query_bot.DB_PATH = _make_db_for_official_downrate_tests(rows)
        reply = query_bot.all_groups_official_downrate_reply()
        for label in query_bot.OFFICIAL_GROUP_LABELS:
            self.assertIn(f"{label}(CPIS官方GROUP彙總)", reply)

    def test_date_applies_to_every_group(self):
        query_bot.DB_PATH = _make_db_for_official_downrate_tests([])
        reply = query_bot.all_groups_official_downrate_reply(date_ymd="20260809", date_label="08/09")
        self.assertEqual(reply.count("08/09查無官方GROUP彙總資料"), len(query_bot.OFFICIAL_GROUP_LABELS))


class TestGetStdHours(unittest.TestCase):
    """2026/08/09使用者提供：CED-1(頂針)2.3hr、CED-M2/M3/M4(Multi step)2.9hr，
    跟hourly_push.py保持一致。"""

    def test_ced_1_uses_generic_ced_prefix_2_3(self):
        self.assertEqual(query_bot.get_std_hours("CED-1"), 2.3)

    def test_ced_m2_m3_m4_use_2_9_not_generic_ced_prefix(self):
        self.assertEqual(query_bot.get_std_hours("CED-M2"), 2.9)
        self.assertEqual(query_bot.get_std_hours("CED-M3"), 2.9)
        self.assertEqual(query_bot.get_std_hours("CED-M4"), 2.9)


def _make_db_for_changeover_tests(rows):
    """rows是list of dict，可含machine_id/e_tag/job_code/engineer_id/dur/
    wait_dur/end_date/end_time(缺的欄位當NULL)，寫進ee_maintenance_record。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            machine_id TEXT, bgn_date TEXT, bgn_time TEXT, end_date TEXT, end_time TEXT,
            job_code TEXT, e_tag TEXT, engineer_id TEXT, dur REAL, wait_dur REAL
        )
    """)
    for row in rows:
        conn.execute(
            "INSERT INTO ee_maintenance_record "
            "(machine_id, bgn_date, bgn_time, end_date, end_time, job_code, e_tag, engineer_id, dur, wait_dur) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row.get("machine_id"), row.get("bgn_date"), row.get("bgn_time"), row.get("end_date"),
             row.get("end_time"), row.get("job_code"), row.get("e_tag"), row.get("engineer_id"),
             row.get("dur"), row.get("wait_dur")),
        )
    conn.commit()
    conn.close()
    return path


class TestGroupChangeoverDetailReply(unittest.TestCase):
    """「<群組>改機」查詢：今日該群組改機台數＋CED/CEE/CD分類平均工時＋依人員
    (工號)分類明細(2026/08/09使用者要求)。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH
        # 隔離掉真正隨repo一起發布的engineer_master.json，避免這裡的假工號
        # 剛好在真實名冊裡對到人名，讓測試斷言變得不穩定(工號後面+姓名的
        # 行為另外有TestGroupChangeoverDetailReplyEngineerName驗證)
        self._orig_master_path = engineer_master.PATH
        self._orig_master_cache = engineer_master._cache
        engineer_master.PATH = "/tmp/does_not_exist_engineer_master_test.json"
        engineer_master._cache = None

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path
        engineer_master.PATH = self._orig_master_path
        engineer_master._cache = self._orig_master_cache

    def test_shows_total_count_category_avg_and_per_engineer_breakdown(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.0},
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "end_time": "11:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.4},
            {"machine_id": "BAA03", "e_tag": "S", "end_date": today, "end_time": "12:00",
             "job_code": "CEE", "engineer_id": "s10435", "dur": 1.0},
        ])
        reply = query_bot.group_changeover_detail_reply("DB", now)
        self.assertIn("【DB改機】今日共3台", reply)
        self.assertIn("CED機台2台平均1.2hr", reply)
        self.assertIn("CEE機台1台平均1.0hr", reply)
        self.assertIn("s10435  改機3台", reply)

    def test_machine_detail_section_lists_each_machine_with_wait_and_duration(self):
        # 2026/08/10使用者要求：打DB改機/2100改機/LOC改機這種<群組>改機查詢，
        # 要多一段「機台明細」逐台列出wait時間+改機時間+機台號碼+人員工號，
        # 不能只有彙總統計
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.5, "wait_dur": 0.5},
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "11:00",
             "job_code": "CEE", "engineer_id": None, "dur": 2.0, "wait_dur": None},
        ])
        reply = query_bot.group_changeover_detail_reply("DB", now)
        self.assertIn("機台明細:", reply)
        # 依機台代號排序，BAA01排在BAA02前面
        detail_section = reply.split("機台明細:")[1]
        self.assertIn("BAA01  待?  改機2.00hr  未指定", detail_section)
        self.assertIn("BAA02  待0.50hr  改機1.50hr  s10435", detail_section)
        self.assertLess(detail_section.index("BAA01"), detail_section.index("BAA02"))

    def test_non_changeover_jcode_excluded(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "INK", "engineer_id": "s10435", "dur": 0.5},
        ])
        reply = query_bot.group_changeover_detail_reply("DB", now)
        self.assertIn("今日目前沒有完成的改機紀錄", reply)

    def test_flipchip_internal_code_fc_maps_to_display_name(self):
        # _group_for_machine()對FlipChip機台回傳內部代號"FC"，顯示文字要轉成
        # "FlipChip"，2026/08/09發現這個坑，避免"FlipChip"字面比對不到"FC"
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BA512", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "27512", "dur": 1.0},
        ])
        reply = query_bot.group_changeover_detail_reply("FC", now)
        self.assertIn("【FlipChip改機】今日共1台", reply)

    def test_epoxy_combines_esec_and_db_but_not_loc(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BA205", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "e1", "dur": 1.0},   # ESEC
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "e2", "dur": 1.0},   # DB
            {"machine_id": "BA801", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "e3", "dur": 1.0},   # LOC，不算EPOXY
        ])
        reply = query_bot.group_changeover_detail_reply("EPOXY", now)
        self.assertIn("【EPOXY改機】今日共2台", reply)

    def test_loc_uses_cn_cd_standard_not_ced_cee(self):
        # LOC(CM700設備)的真正改機代碼是CN/CD家族，跟ESEC/DB的CED/CEE不一樣
        # (2026/08/09使用者確認)
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BA801", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CN", "engineer_id": "e1", "dur": 1.0},
            {"machine_id": "BA802", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CNO", "engineer_id": "e2", "dur": 2.0},
            {"machine_id": "BA803", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CD", "engineer_id": "e3", "dur": 0.5},
            # CED不是LOC認得的代碼，不算改機
            {"machine_id": "BA804", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "e4", "dur": 1.0},
        ])
        reply = query_bot.group_changeover_detail_reply("LOC", now)
        self.assertIn("【LOC改機】今日共3台", reply)
        self.assertIn("CN機台2台平均1.5hr", reply)
        self.assertIn("CD機台1台平均0.5hr", reply)

    def test_shows_day_and_night_shift_breakdown(self):
        # 早班07:30~19:30／夜班19:30~次日07:30，依end_time判斷
        # (2026/08/10使用者要求，跟hourly_push推播的早班/夜班統計一致)
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.0},   # 早班
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "end_time": "18:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.4},   # 早班
            {"machine_id": "BAA03", "e_tag": "S", "end_date": today, "end_time": "22:00",
             "job_code": "CEE", "engineer_id": "s10435", "dur": 1.0},   # 夜班
        ])
        reply = query_bot.group_changeover_detail_reply("DB", now)
        self.assertIn("【DB改機】今日共3台", reply)
        self.assertIn("早班2台 夜班1台", reply)

    def test_shift_breakdown_omits_zero_shift(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.0},
        ])
        reply = query_bot.group_changeover_detail_reply("DB", now)
        self.assertIn("早班1台", reply)
        self.assertNotIn("夜班", reply)

    def test_engineer_name_appended_in_engineer_breakdown(self):
        # 2026/08/10使用者要求：工號後面要補上姓名
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.0},
        ])
        engineer_master.PATH = _make_engineer_master([("10435", "王小明", "EE")])
        engineer_master._cache = None
        reply = query_bot.group_changeover_detail_reply("DB", now)
        self.assertIn("s10435(王小明)  改機1台", reply)

    def test_mfg_ee_breakdown(self):
        # 2026/08/10使用者要求：改機統計分成MFG(產線)/EE(設備)
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "10001", "dur": 1.0},   # OP=MFG
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "end_time": "11:00",
             "job_code": "CED", "engineer_id": "20002", "dur": 1.0},   # EE
            {"machine_id": "BAA03", "e_tag": "S", "end_date": today, "end_time": "12:00",
             "job_code": "CED", "engineer_id": "99999", "dur": 1.0},   # 查無資料
        ])
        engineer_master.PATH = _make_engineer_master([
            ("10001", "王小明", "OP"), ("20002", "李大華", "EE"),
        ])
        engineer_master._cache = None
        reply = query_bot.group_changeover_detail_reply("DB", now)
        self.assertIn("MFG1台 EE1台 未知1台", reply)

    def test_date_label_replaces_today_wording(self):
        # 2026/08/10使用者要求：指定日期查詢(例如"8/9DB改機")要顯示那個
        # 日期而不是"今日"
        now = datetime.datetime(2026, 8, 9, 12, 0)
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BAA01", "e_tag": "S", "end_date": "2026-08-09", "end_time": "10:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.0},
        ])
        reply = query_bot.group_changeover_detail_reply("DB", now, date_label="08/09")
        self.assertIn("【DB改機】08/09共1台", reply)
        self.assertNotIn("今日", reply)

    def test_date_label_on_empty_result(self):
        now = datetime.datetime(2026, 8, 9, 12, 0)
        query_bot.DB_PATH = _make_db_for_changeover_tests([])
        reply = query_bot.group_changeover_detail_reply("DB", now, date_label="08/09")
        self.assertIn("08/09目前沒有完成的改機紀錄", reply)


class TestAllChangeoverReply(unittest.TestCase):
    """「改機」查詢(不指定群組時)：列出EPOXY/LOC/FlipChip全部群組指定日期
    (預設今日)的改機彙總(2026/08/10使用者要求)。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH
        self._orig_master_path = engineer_master.PATH
        self._orig_master_cache = engineer_master._cache
        engineer_master.PATH = "/tmp/does_not_exist_engineer_master_test.json"
        engineer_master._cache = None

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path
        engineer_master.PATH = self._orig_master_path
        engineer_master._cache = self._orig_master_cache

    def test_lists_all_groups(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "e1", "dur": 1.0},   # DB(EPOXY)
            {"machine_id": "BA801", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CN", "engineer_id": "e2", "dur": 1.0},    # LOC
            {"machine_id": "BA512", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "e3", "dur": 1.0},   # FlipChip
        ])
        reply = query_bot.all_changeover_reply(now)
        self.assertIn("【EPOXY改機】今日共1台", reply)
        self.assertIn("【LOC改機】今日共1台", reply)
        self.assertIn("【FlipChip改機】今日共1台", reply)

    def test_date_label_applies_to_all_sections(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        query_bot.DB_PATH = _make_db_for_changeover_tests([])
        reply = query_bot.all_changeover_reply(now, date_label="08/09")
        self.assertNotIn("今日", reply)
        self.assertEqual(reply.count("08/09目前沒有完成的改機紀錄"), 3)


def _make_db_for_machine_changeover_tests(ee_rows=None, pm_rows=None):
    """跟_make_db_for_changeover_tests()類似，但多帶wait_dur欄位、還能選擇
    寫進pm_monitor_record(供「<機台代號>改機」查詢的即時待改狀態測試用)。
    ee_rows是list of dict(可含machine_id/e_tag/job_code/engineer_id/dur/
    wait_dur/end_date/end_time)；pm_rows是list of dict(entity/status/in_time)。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            machine_id TEXT, bgn_date TEXT, bgn_time TEXT, end_date TEXT, end_time TEXT,
            job_code TEXT, e_tag TEXT, engineer_id TEXT, dur REAL, wait_dur REAL
        )
    """)
    for row in (ee_rows or []):
        conn.execute(
            "INSERT INTO ee_maintenance_record "
            "(machine_id, bgn_date, bgn_time, end_date, end_time, job_code, e_tag, engineer_id, dur, wait_dur) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row.get("machine_id"), row.get("bgn_date"), row.get("bgn_time"), row.get("end_date"),
             row.get("end_time"), row.get("job_code"), row.get("e_tag"), row.get("engineer_id"),
             row.get("dur"), row.get("wait_dur")),
        )
    conn.execute("""
        CREATE TABLE pm_monitor_record (
            oper TEXT, entity TEXT, model TEXT, status TEXT,
            lot_no TEXT, bond_id TEXT, wip TEXT, in_time TEXT,
            outplan TEXT, jcode TEXT, operator TEXT, fetched_at TEXT
        )
    """)
    pm_fetched_at = "2026-08-09T17:00:00"
    for pm_row in (pm_rows or []):
        conn.execute(
            "INSERT INTO pm_monitor_record (entity, status, in_time, fetched_at) VALUES (?,?,?,?)",
            (pm_row.get("entity"), pm_row.get("status"), pm_row.get("in_time"), pm_fetched_at),
        )
    conn.commit()
    conn.close()
    return path


class TestMachineChangeoverDetailReply(unittest.TestCase):
    """「<機台代號>改機」查詢(2026/08/10使用者要求)：單一機台改機次數＋
    分類平均改機時間＋待改時間(即時＋歷史平均)＋改機人員。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH
        self._orig_master_path = engineer_master.PATH
        self._orig_master_cache = engineer_master._cache
        engineer_master.PATH = "/tmp/does_not_exist_engineer_master_test.json"
        engineer_master._cache = None

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path
        engineer_master.PATH = self._orig_master_path
        engineer_master._cache = self._orig_master_cache

    def test_unknown_machine_group_returns_error_message(self):
        query_bot.DB_PATH = _make_db_for_machine_changeover_tests()
        reply = query_bot.machine_changeover_detail_reply("ZZ999")
        self.assertIn("查無所屬機型群組", reply)

    def test_no_records_today(self):
        query_bot.DB_PATH = _make_db_for_machine_changeover_tests()
        reply = query_bot.machine_changeover_detail_reply("BAA02")
        self.assertIn("【BAA02改機】今日", reply)
        self.assertIn("目前沒有完成的改機紀錄", reply)

    def test_counts_avg_and_engineer_breakdown_for_today(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_machine_changeover_tests(ee_rows=[
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "end_time": "09:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.0, "wait_dur": 0.5},
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "end_time": "11:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.4, "wait_dur": 1.5},
            # 不同機台的紀錄不能算進BAA02自己的統計
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "10:00",
             "job_code": "CED", "engineer_id": "s10488", "dur": 9.0, "wait_dur": 9.0},
        ])
        reply = query_bot.machine_changeover_detail_reply("BAA02", now)
        self.assertIn("【BAA02改機】今日", reply)
        self.assertIn("改機次數: 2次", reply)
        self.assertIn("CEDx2次平均1.2hr", reply)
        self.assertIn("歷史平均等待改機時間: 1.00hr", reply)
        self.assertIn("s10435  改機2次", reply)
        self.assertNotIn("s10488", reply)

    def test_non_changeover_jcode_excluded_from_count(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_machine_changeover_tests(ee_rows=[
            {"machine_id": "BAA02", "e_tag": "S", "end_date": today, "end_time": "09:00",
             "job_code": "INK", "engineer_id": "s10435", "dur": 0.2, "wait_dur": 0.1},
        ])
        reply = query_bot.machine_changeover_detail_reply("BAA02", now)
        self.assertIn("目前沒有完成的改機紀錄", reply)

    def test_all_history_flag_ignores_date_and_includes_older_records(self):
        query_bot.DB_PATH = _make_db_for_machine_changeover_tests(ee_rows=[
            {"machine_id": "BAA02", "e_tag": "S", "end_date": "2026-01-01", "end_time": "09:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.0, "wait_dur": 0.5},
            {"machine_id": "BAA02", "e_tag": "S", "end_date": "2026-08-09", "end_time": "09:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.0, "wait_dur": 0.5},
        ])
        reply = query_bot.machine_changeover_detail_reply("BAA02", all_history=True)
        self.assertIn("【BAA02改機】全部歷史紀錄", reply)
        self.assertIn("改機次數: 2次", reply)

    def test_live_wait_setup_shows_elapsed_hours(self):
        # _elapsed_hours_since()是拿目前真實時間算的(跟live_status_reply()
        # 既有邏輯一致)，這裡不比對確切時數(那樣要mock datetime、跟現有
        # 測試風格不一致)，只驗證有秀出「已等待」+單位hr的字樣
        now = datetime.datetime(2026, 8, 9, 14, 0)
        query_bot.DB_PATH = _make_db_for_machine_changeover_tests(pm_rows=[
            {"entity": "BAA02", "status": "WAIT-SETUP", "in_time": "2026/08/09 12:00"},
        ])
        reply = query_bot.machine_changeover_detail_reply("BAA02", now)
        self.assertIn("待改(即時): 目前等待改機中，已等待", reply)
        self.assertIn("hr", reply.split("待改(即時):")[1])

    def test_no_live_wait_when_not_in_wait_setup_status(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        query_bot.DB_PATH = _make_db_for_machine_changeover_tests(pm_rows=[
            {"entity": "BAA02", "status": "IN-REPAIR", "in_time": "2026/08/09 12:00"},
        ])
        reply = query_bot.machine_changeover_detail_reply("BAA02", now)
        self.assertIn("待改(即時): 目前無等待改機中紀錄", reply)


class TestWorkhoursReply(unittest.TestCase):
    """「工時」查詢：今日各工號人員修機+改機總工時(2026/08/09使用者要求)。"""

    def setUp(self):
        self._orig_db_path = query_bot.DB_PATH
        # 隔離掉真正隨repo一起發布的engineer_master.json，避免這裡的假工號
        # 剛好在真實名冊裡對到人名，讓測試斷言變得不穩定
        self._orig_master_path = engineer_master.PATH
        self._orig_master_cache = engineer_master._cache
        engineer_master.PATH = "/tmp/does_not_exist_engineer_master_test.json"
        engineer_master._cache = None

    def tearDown(self):
        query_bot.DB_PATH = self._orig_db_path
        engineer_master.PATH = self._orig_master_path
        engineer_master._cache = self._orig_master_cache

    def test_sums_repair_and_changeover_hours_for_one_engineer(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BA205", "e_tag": "R", "end_date": today, "end_time": "10:00",
             "engineer_id": "s10435", "dur": 2.0},
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "11:00",
             "job_code": "CED", "engineer_id": "s10435", "dur": 1.5},
        ])
        reply = query_bot.workhours_reply("s10435", now)
        self.assertIn("s10435  修機2.0hr + 改機1.5hr = 總3.5hr", reply)

    def test_non_changeover_jcode_excluded_from_changeover_hours(self):
        # e_tag='S'但job_code是INK這種生產中小動作，不算進改機工時
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BA205", "e_tag": "R", "end_date": today, "end_time": "10:00",
             "engineer_id": "s10435", "dur": 2.0},
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "11:00",
             "job_code": "INK", "engineer_id": "s10435", "dur": 1.5},
        ])
        reply = query_bot.workhours_reply("s10435", now)
        self.assertIn("s10435  修機2.0hr + 改機0.0hr = 總2.0hr", reply)

    def test_no_engineer_id_lists_everyone_sorted_by_total_hours(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BA205", "e_tag": "R", "end_date": today, "end_time": "10:00",
             "engineer_id": "s10435", "dur": 2.0},
            {"machine_id": "BAA01", "e_tag": "S", "end_date": today, "end_time": "11:00",
             "job_code": "CED", "engineer_id": "27512", "dur": 1.0},
        ])
        reply = query_bot.workhours_reply(None, now)
        self.assertIn("s10435", reply)
        self.assertIn("27512", reply)

    def test_no_records_shows_placeholder(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        query_bot.DB_PATH = _make_db_for_changeover_tests([])
        reply = query_bot.workhours_reply("s10435", now)
        self.assertIn("今日目前沒有修機/改機紀錄", reply)

    def test_engineer_name_appended(self):
        # 2026/08/10使用者要求：工號後面要補上姓名
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BA205", "e_tag": "R", "end_date": today, "end_time": "10:00",
             "engineer_id": "s10435", "dur": 2.0},
        ])
        engineer_master.PATH = _make_engineer_master([("10435", "王小明", "EE")])
        engineer_master._cache = None
        reply = query_bot.workhours_reply(None, now)
        self.assertIn("s10435(王小明)  修機2.0hr", reply)

    def test_date_label_replaces_today_wording(self):
        # 2026/08/10使用者要求：指定日期查詢(例如"8/9工時")要顯示那個日期
        # 而不是"今日"
        now = datetime.datetime(2026, 8, 9, 14, 0)
        today = now.date().isoformat()
        query_bot.DB_PATH = _make_db_for_changeover_tests([
            {"machine_id": "BA205", "e_tag": "R", "end_date": today, "end_time": "10:00",
             "engineer_id": "s10435", "dur": 2.0},
        ])
        reply = query_bot.workhours_reply(None, now, date_label="08/09")
        self.assertIn("【工時】08/09修機+改機總工時", reply)
        self.assertNotIn("今日", reply)

    def test_date_label_on_empty_result(self):
        now = datetime.datetime(2026, 8, 9, 14, 0)
        query_bot.DB_PATH = _make_db_for_changeover_tests([])
        reply = query_bot.workhours_reply("s10435", now, date_label="08/09")
        self.assertIn("08/09目前沒有修機/改機紀錄", reply)


if __name__ == "__main__":
    unittest.main()
