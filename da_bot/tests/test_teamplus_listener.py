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


class TestParseQueryDownrate(unittest.TestCase):
    """downrate關鍵字判斷曾經因為兩處regex不一致，"DOWN RATE"(有空格)會漏比對到，
    落到預設的完整資訊模式而不是downrate。這裡鎖定空格/大小寫的各種寫法都要正確。"""

    def test_no_space_lowercase(self):
        self.assertEqual(listener.parse_query("BAA08downrate")["mode"], "downrate")

    def test_with_space_uppercase(self):
        self.assertEqual(listener.parse_query("BAA08 DOWN RATE")["mode"], "downrate")

    def test_multiple_spaces(self):
        self.assertEqual(listener.parse_query("BAA08 down   rate")["mode"], "downrate")

    def test_chinese_keyword_variant(self):
        self.assertEqual(listener.parse_query("BAA08停機明細")["mode"], "downrate")

    def test_official_group_downrate_still_takes_priority_over_machine_code(self):
        # "DB800"本身也會被MACHINE_RE誤判成機台代號，但downrate關鍵字+官方群組名稱
        # 要優先判斷成group_official_downrate，不能被機台規則搶先攔截
        cmd = listener.parse_query("DB800 down rate")
        self.assertEqual(cmd, {"mode": "group_official_downrate", "group_label": "DB800"})


if __name__ == "__main__":
    unittest.main()
