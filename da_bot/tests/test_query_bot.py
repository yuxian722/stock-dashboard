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
