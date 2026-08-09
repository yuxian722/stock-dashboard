"""db.py 的離線單元測試：用 in-memory SQLite 驗證建表與 upsert 去重。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

import db


class TestUpsertRows(unittest.TestCase):
    def test_dedup(self):
        conn = db.get_connection(":memory:")
        db.init_ee_maintenance_table(conn)

        rows = [["2026-08-01", "A2", "DA"], ["2026-08-01", "A2", "DB"]]
        inserted = db.upsert_rows(
            conn, "ee_maintenance_record", rows, "2026-08-01", "2026-08-09", "ee_entity", "DA"
        )
        self.assertEqual(inserted, 2)

        # 同樣的 rows 再寫一次應該全部被去重
        inserted_again = db.upsert_rows(
            conn, "ee_maintenance_record", rows, "2026-08-01", "2026-08-09", "ee_entity", "DA"
        )
        self.assertEqual(inserted_again, 0)

        count = conn.execute("SELECT COUNT(*) FROM ee_maintenance_record").fetchone()[0]
        self.assertEqual(count, 2)

    def test_utilization_table(self):
        conn = db.get_connection(":memory:")
        db.init_utilization_table(conn)

        rows = [["A2", "DA", "95%"]]
        inserted = db.upsert_rows(
            conn, "utilization_analysis", rows, "2026-08-01", "2026-08-09", "util_oper", "DA"
        )
        self.assertEqual(inserted, 1)

    def test_rejects_unknown_table(self):
        conn = db.get_connection(":memory:")
        with self.assertRaises(ValueError):
            db.upsert_rows(conn, "not_a_table", [], "2026-08-01", "2026-08-09", "x", "y")


if __name__ == "__main__":
    unittest.main()
