"""engineer_master.py的離線單元測試：工號→姓名/部門(MFG/EE)對照，
用暫存JSON驗證正規化比對(不同工號寫法要對到同一筆)、姓名補上格式、
以及dept_breakdown()的MFG/EE/未知分類(2026/08/10使用者要求)。"""

import conftest  # noqa: F401  (設定 sys.path)

import json
import tempfile
import unittest

import engineer_master


def _make_master(entries):
    """entries是list of (key, name, dept)，寫成engineer_master.json格式的暫存檔。"""
    path = tempfile.mktemp(suffix=".json")
    data = {key: {"name": name, "dept": dept} for key, name, dept in entries}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


class TestNormalizeId(unittest.TestCase):
    def test_strips_s_prefix_and_leading_zeros_and_uppercases(self):
        self.assertEqual(engineer_master._normalize_id("S010435"), "10435")
        self.assertEqual(engineer_master._normalize_id("s10435"), "10435")
        self.assertEqual(engineer_master._normalize_id("10435"), "10435")
        self.assertEqual(engineer_master._normalize_id("S10435"), "10435")

    def test_none_and_empty(self):
        self.assertEqual(engineer_master._normalize_id(None), "")
        self.assertEqual(engineer_master._normalize_id(""), "")


class TestGetEngineer(unittest.TestCase):
    def setUp(self):
        self._orig_path = engineer_master.PATH
        self._orig_cache = engineer_master._cache

    def tearDown(self):
        engineer_master.PATH = self._orig_path
        engineer_master._cache = self._orig_cache

    def test_looks_up_by_normalized_id_regardless_of_prefix(self):
        engineer_master.PATH = _make_master([("10435", "王小明", "EE")])
        engineer_master._cache = None
        self.assertEqual(engineer_master.get_engineer_name("s10435"), "王小明")
        self.assertEqual(engineer_master.get_engineer_name("S10435"), "王小明")
        self.assertEqual(engineer_master.get_engineer_name("10435"), "王小明")

    def test_unknown_id_returns_none(self):
        engineer_master.PATH = _make_master([("10435", "王小明", "EE")])
        engineer_master._cache = None
        self.assertIsNone(engineer_master.get_engineer_name("99999"))

    def test_missing_file_returns_empty_lookup_not_crash(self):
        engineer_master.PATH = "/tmp/does_not_exist_engineer_master.json"
        engineer_master._cache = None
        self.assertIsNone(engineer_master.get_engineer_name("10435"))

    def test_dept_label_op_maps_to_mfg(self):
        engineer_master.PATH = _make_master([
            ("10435", "王小明", "OP"), ("20000", "李大華", "EE"), ("30000", "陳工程", "PE"),
        ])
        engineer_master._cache = None
        self.assertEqual(engineer_master.get_engineer_dept_label("10435"), "MFG")
        self.assertEqual(engineer_master.get_engineer_dept_label("20000"), "EE")
        self.assertIsNone(engineer_master.get_engineer_dept_label("30000"))
        self.assertIsNone(engineer_master.get_engineer_dept_label("99999"))


class TestFormatEngineer(unittest.TestCase):
    def setUp(self):
        self._orig_path = engineer_master.PATH
        self._orig_cache = engineer_master._cache

    def tearDown(self):
        engineer_master.PATH = self._orig_path
        engineer_master._cache = self._orig_cache

    def test_appends_name_when_found(self):
        engineer_master.PATH = _make_master([("10435", "王小明", "EE")])
        engineer_master._cache = None
        self.assertEqual(engineer_master.format_engineer("s10435"), "s10435(王小明)")

    def test_falls_back_to_bare_id_when_not_found(self):
        engineer_master.PATH = _make_master([])
        engineer_master._cache = None
        self.assertEqual(engineer_master.format_engineer("未指定"), "未指定")
        self.assertEqual(engineer_master.format_engineer("s99999"), "s99999")

    def test_empty_id_returns_empty(self):
        self.assertEqual(engineer_master.format_engineer(None), "")
        self.assertEqual(engineer_master.format_engineer(""), "")


class TestDeptBreakdown(unittest.TestCase):
    def setUp(self):
        self._orig_path = engineer_master.PATH
        self._orig_cache = engineer_master._cache

    def tearDown(self):
        engineer_master.PATH = self._orig_path
        engineer_master._cache = self._orig_cache

    def test_counts_mfg_ee_and_unknown(self):
        engineer_master.PATH = _make_master([
            ("10435", "王小明", "OP"), ("20000", "李大華", "EE"), ("30000", "陳工程", "PE"),
        ])
        engineer_master._cache = None
        counts = engineer_master.dept_breakdown(["10435", "10435", "20000", "30000", "99999"])
        self.assertEqual(counts, {"MFG": 2, "EE": 1, "未知": 2})

    def test_empty_list(self):
        engineer_master.PATH = _make_master([])
        engineer_master._cache = None
        self.assertEqual(engineer_master.dept_breakdown([]), {"MFG": 0, "EE": 0, "未知": 0})


if __name__ == "__main__":
    unittest.main()
