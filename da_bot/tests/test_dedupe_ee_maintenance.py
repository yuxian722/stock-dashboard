"""dedupe_ee_maintenance.py 的離線單元測試(不連網)：驗證重複紀錄偵測跟刪除邏輯。"""

import conftest  # noqa: F401  (設定 sys.path)

import sqlite3
import tempfile
import unittest

import dedupe_ee_maintenance as dedupe_mod


def _make_db(rows):
    """rows是list of dict，缺的欄位當NULL，寫進一個帶id自動編號的ee_maintenance_record表。"""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ee_maintenance_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prod_line TEXT, oper TEXT, model TEXT, machine_id TEXT,
            wait_date TEXT, wait_time TEXT, bgn_date TEXT, bgn_time TEXT,
            end_date TEXT, end_time TEXT, wait_dur REAL, dur REAL,
            engineer_id TEXT, e_tag TEXT, job_code TEXT, owner TEXT,
            tool_number TEXT, cause TEXT, description TEXT, con_lot_no TEXT,
            std REAL, bd_id TEXT, product TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cols = dedupe_mod._NATURAL_KEY_COLUMNS
    for row in rows:
        conn.execute(
            f"INSERT INTO ee_maintenance_record ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            tuple(row.get(c) for c in cols),
        )
    conn.commit()
    return conn


def _record(machine_id="BA205", bgn_date="2026-08-09"):
    return {
        "prod_line": "APG", "oper": "DA", "model": "DIE-ATTACH", "machine_id": machine_id,
        "bgn_date": bgn_date, "bgn_time": "10:00", "end_date": bgn_date, "end_time": "12:00",
        "wait_dur": 0.0, "dur": 2.0, "engineer_id": "ENG1", "e_tag": "S", "job_code": "CED",
    }


class TestFindDuplicateCount(unittest.TestCase):
    def test_no_duplicates(self):
        conn = _make_db([_record(machine_id="BA205"), _record(machine_id="BA206")])
        total, dup = dedupe_mod.find_duplicate_count(conn)
        self.assertEqual(total, 2)
        self.assertEqual(dup, 0)
        conn.close()

    def test_counts_duplicates(self):
        conn = _make_db([_record(), _record(), _record()])
        total, dup = dedupe_mod.find_duplicate_count(conn)
        self.assertEqual(total, 3)
        self.assertEqual(dup, 2)
        conn.close()

    def test_empty_table(self):
        conn = _make_db([])
        total, dup = dedupe_mod.find_duplicate_count(conn)
        self.assertEqual(total, 0)
        self.assertEqual(dup, 0)
        conn.close()


class TestDedupe(unittest.TestCase):
    def test_keeps_one_copy_of_each_duplicate_group(self):
        conn = _make_db([_record(), _record(), _record()])
        removed = dedupe_mod.dedupe(conn)
        self.assertEqual(removed, 2)
        remaining = conn.execute("SELECT COUNT(*) FROM ee_maintenance_record").fetchone()[0]
        self.assertEqual(remaining, 1)
        conn.close()

    def test_distinct_records_all_kept(self):
        conn = _make_db([_record(machine_id="BA205"), _record(machine_id="BA206"), _record(machine_id="BA207")])
        removed = dedupe_mod.dedupe(conn)
        self.assertEqual(removed, 0)
        remaining = conn.execute("SELECT COUNT(*) FROM ee_maintenance_record").fetchone()[0]
        self.assertEqual(remaining, 3)
        conn.close()

    def test_mixed_duplicate_and_distinct_records(self):
        conn = _make_db([
            _record(machine_id="BA205"), _record(machine_id="BA205"),  # 重複
            _record(machine_id="BA206"),                                # 沒重複
        ])
        removed = dedupe_mod.dedupe(conn)
        self.assertEqual(removed, 1)
        machine_ids = [r[0] for r in conn.execute("SELECT machine_id FROM ee_maintenance_record")]
        self.assertEqual(sorted(machine_ids), ["BA205", "BA206"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
