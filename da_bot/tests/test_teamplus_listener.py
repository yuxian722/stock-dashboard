"""teamplus_listener.py 的離線單元測試(不連網)：指令解析。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

import teamplus_listener as listener


class TestParseQueryDefaultMode(unittest.TestCase):
    def test_bare_machine_code_defaults_to_full(self):
        self.assertEqual(listener.parse_query("BAA08"), {"machine": "BAA08", "mode": "full"})

    def test_status_keyword_also_defaults_to_full(self):
        # 「故障」「狀態」這類詞沒有專屬分支，一樣落到完整資訊
        cmd = listener.parse_query("BAA08狀態")
        self.assertEqual(cmd["mode"], "full")

    def test_today_keyword_uses_detail_mode(self):
        cmd = listener.parse_query("BA220今天")
        self.assertEqual(cmd["mode"], "detail")

    def test_no_machine_code_returns_none(self):
        self.assertIsNone(listener.parse_query("hello there"))


if __name__ == "__main__":
    unittest.main()
