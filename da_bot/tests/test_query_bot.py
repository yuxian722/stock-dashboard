"""query_bot.py 的離線單元測試(不連網)：用暫存SQLite驗證full_info_reply()把
即時狀態/統計摘要/稼動率/設備健康正確組在一起(健康監控資料表不存在時要跳過，
不要噴錯)。"""

import conftest  # noqa: F401  (設定 sys.path)

import sqlite3
import tempfile
import unittest

import query_bot


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
    pm_rows: list of (entity, status)寫進pm_monitor_record同一批fetched_at；
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
        for entity, status in (pm_rows or []):
            conn.execute(
                "INSERT INTO pm_monitor_record (entity, status, fetched_at) VALUES (?,?,?)",
                (entity, status, pm_fetched_at),
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


class TestGetStdHours(unittest.TestCase):
    """2026/08/09使用者提供：CED-1(頂針)2.3hr、CED-M2/M3/M4(Multi step)2.9hr，
    跟hourly_push.py保持一致。"""

    def test_ced_1_uses_generic_ced_prefix_2_3(self):
        self.assertEqual(query_bot.get_std_hours("CED-1"), 2.3)

    def test_ced_m2_m3_m4_use_2_9_not_generic_ced_prefix(self):
        self.assertEqual(query_bot.get_std_hours("CED-M2"), 2.9)
        self.assertEqual(query_bot.get_std_hours("CED-M3"), 2.9)
        self.assertEqual(query_bot.get_std_hours("CED-M4"), 2.9)


if __name__ == "__main__":
    unittest.main()
