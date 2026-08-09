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


class TestHelpTrigger(unittest.TestCase):
    def test_chinese_trigger(self):
        self.assertEqual(listener.parse_query("查詢"), {"mode": "help"})

    def test_english_trigger_case_insensitive(self):
        self.assertEqual(listener.parse_query("HELP"), {"mode": "help"})
        self.assertEqual(listener.parse_query("Help"), {"mode": "help"})

    def test_other_trigger_words(self):
        for word in ("說明", "指令", "用法", "選單", "?", "？"):
            self.assertEqual(listener.parse_query(word), {"mode": "help"}, msg=word)

    def test_trigger_word_as_part_of_longer_message_not_matched(self):
        # 只有整句完全等於觸發字才叫出說明清單，不要在正常查詢句子裡誤觸發
        cmd = listener.parse_query("BAA08查詢一下狀態")
        self.assertNotEqual(cmd.get("mode"), "help")

    def test_build_reply_returns_help_text(self):
        reply = listener.build_reply({"mode": "help"})
        self.assertEqual(reply, listener.HELP_TEXT)
        self.assertIn("今天", reply)


class TestPollOnceFloodProtection(unittest.TestCase):
    """
    poll_once()以前偵測到疑似自問自答/洗版時會sys.exit(1)，把整支服務(連同
    整點推播)一起殺死，之後除非有人發現、手動重開，機器人會一直保持沒反應。
    這裡鎖定：不管洗版怎麼發生，poll_once()都不能讓整個程序當掉。
    """

    def setUp(self):
        self._orig_read = listener.teamplus_api.read_new_messages
        self._orig_send = listener.teamplus_api.send_message

    def tearDown(self):
        listener.teamplus_api.read_new_messages = self._orig_read
        listener.teamplus_api.send_message = self._orig_send

    def test_flood_within_one_batch_does_not_raise_systemexit(self):
        flood_size = listener.MAX_REPLIES_PER_WINDOW + 3
        texts = ["查詢"] * flood_size

        listener.teamplus_api.read_new_messages = lambda cursor: (texts, "cursor-1")
        sent = []
        listener.teamplus_api.send_message = lambda message, chat_id=None: (sent.append(message) or (True, "ok"))

        state = {"cursor": None, "bot_sent_norms": [], "recent_reply_times": []}
        try:
            listener.poll_once(state)
        except SystemExit:
            self.fail("poll_once() 不應該用sys.exit()把整個服務殺掉")

        # 應該在達到上限那一刻就停手，不是把整批洗版訊息全部回完
        self.assertEqual(len(sent), listener.MAX_REPLIES_PER_WINDOW)

    def test_cursor_still_advances_after_flood_stops_early(self):
        # 就算這批訊息因為洗版保護提早跳出，cursor還是要更新，
        # 不然下次poll_once()會重複讀到同一批舊訊息卡在無限迴圈
        texts = ["查詢"] * (listener.MAX_REPLIES_PER_WINDOW + 3)
        listener.teamplus_api.read_new_messages = lambda cursor: (texts, "cursor-new")
        listener.teamplus_api.send_message = lambda message, chat_id=None: (True, "ok")

        state = {"cursor": "cursor-old", "bot_sent_norms": [], "recent_reply_times": []}
        listener.poll_once(state)
        self.assertEqual(state["cursor"], "cursor-new")


class TestInitListenerStateBootstrap(unittest.TestCase):
    """team+的getNewestMessageList這支API，NewestBatchID傳空字串會直接被拒絕
    (參數錯誤)——這是即時問答從一開始就完全沒反應的真正原因：cursor第一次
    永遠初始化不了。同事逆向出來的teamplus_bot.py改用「先送一則上線通知，
    拿這則訊息真正的batchID當第一個cursor」，這裡鎖定init_listener_state()
    照做，而且完全不會去呼叫read_new_messages(None)這條容易出事的路徑。
    """

    def setUp(self):
        self._orig_send_bid = listener.teamplus_api.send_message_get_batch_id
        self._orig_read = listener.teamplus_api.read_new_messages

    def tearDown(self):
        listener.teamplus_api.send_message_get_batch_id = self._orig_send_bid
        listener.teamplus_api.read_new_messages = self._orig_read

    def test_bootstraps_cursor_from_boot_message_batch_id(self):
        listener.teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (True, "發送成功", "real-batch-id-123")
        )

        def boom(cursor):
            self.fail("開機通知送出成功的話，不該再去呼叫read_new_messages(None)")

        listener.teamplus_api.read_new_messages = boom

        state = listener.init_listener_state()
        self.assertEqual(state["cursor"], "real-batch-id-123")

    def test_boot_message_added_to_dedup_list(self):
        listener.teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (True, "發送成功", "real-batch-id-123")
        )
        listener.teamplus_api.read_new_messages = lambda cursor: self.fail("不該呼叫")

        state = listener.init_listener_state()
        self.assertIn(listener.normalize_for_dedup(listener.BOOT_MESSAGE), state["bot_sent_norms"])

    def test_falls_back_to_read_new_messages_when_boot_message_fails(self):
        # cookie過期之類的狀況，上線通知送不出去，至少服務還是要能啟動
        listener.teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (False, "cookie過期", "unused-bid")
        )
        listener.teamplus_api.read_new_messages = lambda cursor: ([], "fallback-cursor")

        state = listener.init_listener_state()
        self.assertEqual(state["cursor"], "fallback-cursor")


if __name__ == "__main__":
    unittest.main()
