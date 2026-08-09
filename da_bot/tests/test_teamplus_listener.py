"""teamplus_listener.py 的離線單元測試(不連網)：指令解析。"""

import conftest  # noqa: F401  (設定 sys.path)

import tempfile
import unittest

import query_bot
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


class TestParseQueryChangeoverGroupDetail(unittest.TestCase):
    """「<群組>改機」查詢(2026/08/09使用者要求)，必須排在「DB」等機型群組bare
    關鍵字判斷之前，不然"DB改機"會被db_group規則搶先攔截。"""

    def test_db_changeover_takes_priority_over_bare_db_group(self):
        cmd = listener.parse_query("DB改機")
        self.assertEqual(cmd, {"mode": "group_changeover_detail", "group_name": "DB"})

    def test_esec_changeover(self):
        cmd = listener.parse_query("ESEC改機")
        self.assertEqual(cmd, {"mode": "group_changeover_detail", "group_name": "ESEC"})

    def test_loc_changeover(self):
        cmd = listener.parse_query("LOC改機")
        self.assertEqual(cmd, {"mode": "group_changeover_detail", "group_name": "LOC"})

    def test_epoxy_changeover(self):
        cmd = listener.parse_query("EPOXY改機")
        self.assertEqual(cmd, {"mode": "group_changeover_detail", "group_name": "EPOXY"})

    def test_flipchip_changeover_normalizes_to_fc(self):
        cmd = listener.parse_query("FlipChip改機")
        self.assertEqual(cmd, {"mode": "group_changeover_detail", "group_name": "FC"})

    def test_build_reply_dispatches_to_query_bot(self):
        orig_db_path = query_bot.DB_PATH
        query_bot.DB_PATH = tempfile.mktemp(suffix=".db")
        try:
            reply = listener.build_reply({"mode": "group_changeover_detail", "group_name": "DB"})
            # 資料庫裡沒有ee_maintenance_record表時應該回傳錯誤說明文字，不應該整個掛掉
            self.assertIsInstance(reply, str)
        finally:
            query_bot.DB_PATH = orig_db_path


class TestParseQueryWorkhours(unittest.TestCase):
    """「工時」查詢(2026/08/09使用者要求)，必須排在機台代號規則之前，避免
    "s10435"這種帶字母前綴的工號被誤判成機台代號。"""

    def test_engineer_id_with_letter_prefix_before_keyword(self):
        cmd = listener.parse_query("s10435工時")
        self.assertEqual(cmd, {"mode": "workhours", "engineer_id": "s10435"})

    def test_numeric_engineer_id_before_keyword_zong(self):
        cmd = listener.parse_query("27512總工時")
        self.assertEqual(cmd, {"mode": "workhours", "engineer_id": "27512"})

    def test_bare_keyword_with_no_engineer_id(self):
        cmd = listener.parse_query("工時")
        self.assertEqual(cmd, {"mode": "workhours", "engineer_id": None})

    def test_build_reply_dispatches_to_query_bot(self):
        orig_db_path = query_bot.DB_PATH
        query_bot.DB_PATH = tempfile.mktemp(suffix=".db")
        try:
            reply = listener.build_reply({"mode": "workhours", "engineer_id": "s10435"})
            self.assertIsInstance(reply, str)
        finally:
            query_bot.DB_PATH = orig_db_path


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


def _msgs(*texts, start=1):
    """測試用小工具：把純文字清單包成poll_once()現在吃的訊息dict清單，
    每則配一個假的batch_id(b1、b2、...)。"""
    return [{"text": t, "batch_id": f"b{start + i}"} for i, t in enumerate(texts)]


class TestPollOnceFloodProtection(unittest.TestCase):
    """
    poll_once()以前偵測到疑似自問自答/洗版時會sys.exit(1)，把整支服務(連同
    整點推播)一起殺死，之後除非有人發現、手動重開，機器人會一直保持沒反應。
    這裡鎖定：不管洗版怎麼發生，poll_once()都不能讓整個程序當掉。
    """

    def setUp(self):
        self._orig_read = listener.teamplus_api.read_new_messages
        self._orig_send_bid = listener.teamplus_api.send_message_get_batch_id

    def tearDown(self):
        listener.teamplus_api.read_new_messages = self._orig_read
        listener.teamplus_api.send_message_get_batch_id = self._orig_send_bid

    def test_flood_within_one_batch_does_not_raise_systemexit(self):
        flood_size = listener.MAX_REPLIES_PER_WINDOW + 3
        messages = _msgs(*(["查詢"] * flood_size))

        listener.teamplus_api.read_new_messages = lambda cursor: (messages, "cursor-1")
        sent = []
        listener.teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (sent.append(message) or (True, "ok", "reply-bid"))
        )

        state = {"cursor": None, "sent_batch_ids": [], "recent_reply_times": []}
        try:
            listener.poll_once(state)
        except SystemExit:
            self.fail("poll_once() 不應該用sys.exit()把整個服務殺掉")

        # 應該在達到上限那一刻就停手，不是把整批洗版訊息全部回完
        self.assertEqual(len(sent), listener.MAX_REPLIES_PER_WINDOW)

    def test_cursor_still_advances_after_flood_stops_early(self):
        # 就算這批訊息因為洗版保護提早跳出，cursor還是要更新，
        # 不然下次poll_once()會重複讀到同一批舊訊息卡在無限迴圈
        messages = _msgs(*(["查詢"] * (listener.MAX_REPLIES_PER_WINDOW + 3)))
        listener.teamplus_api.read_new_messages = lambda cursor: (messages, "cursor-new")
        listener.teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (True, "ok", "reply-bid")
        )

        state = {"cursor": "cursor-old", "sent_batch_ids": [], "recent_reply_times": []}
        listener.poll_once(state)
        self.assertEqual(state["cursor"], "cursor-new")


class TestPollOnceSelfAnswerLoop(unittest.TestCase):
    """
    實測發現的自問自答bug：機器人回覆內容裡剛好含有查詢關鍵字(例如"downrate"、
    "EPOXY(DB)")，讀回自己剛送出的訊息時，如果只比對「正規化後的文字內容」，
    一旦重新產生的回覆內容跟前一次不是逐字一樣(例如內含的擷取時間不同)，
    dedup就會判斷失敗，把自己的回覆當成新指令，觸發下一輪回覆，兩種回覆
    格式來回觸發、一路洗到防暴衝上限。改用BatchID識別後，不管回覆文字內容
    是什麼，只要是自己送出的訊息，一定跳過，不會被內容誤導。
    """

    def setUp(self):
        self._orig_read = listener.teamplus_api.read_new_messages
        self._orig_send_bid = listener.teamplus_api.send_message_get_batch_id

    def tearDown(self):
        listener.teamplus_api.read_new_messages = self._orig_read
        listener.teamplus_api.send_message_get_batch_id = self._orig_send_bid

    def test_reply_containing_trigger_keywords_is_not_treated_as_new_query(self):
        # 機器人剛送出一則含有"downrate"字樣的回覆(bid="own-reply-1")，
        # 下一輪poll_once()讀回這則訊息時，就算內容完全符合查詢關鍵字規則，
        # 也不該再觸發新的回覆——因為batch_id"own-reply-1"是自己剛送的。
        echoed_own_reply = _msgs(
            "【EPOXY(DB)機型群組】downrate(有資料49/54台): 稼動82.7%", start=1
        )
        listener.teamplus_api.read_new_messages = lambda cursor: (echoed_own_reply, "cursor-2")

        sent = []
        listener.teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (sent.append(message) or (True, "ok", "should-not-be-called"))
        )

        state = {"cursor": "cursor-1", "sent_batch_ids": ["b1"], "recent_reply_times": []}
        listener.poll_once(state)

        self.assertEqual(sent, [])  # 不該再回覆
        self.assertNotIn("b1", state["sent_batch_ids"])  # 用掉一次後要從清單移除

    def test_real_user_message_with_same_text_as_a_past_reply_still_gets_answered(self):
        # 對照組：如果這則訊息的batch_id不是機器人自己送的(代表是別人重新
        # 打了一模一樣的字)，就還是要正常回覆，不能因為文字剛好重複就跳過
        someone_elses_message = _msgs("EPOXY(DB) downrate", start=99)
        listener.teamplus_api.read_new_messages = lambda cursor: (someone_elses_message, "cursor-2")

        sent = []
        listener.teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (sent.append(message) or (True, "ok", "new-reply-bid"))
        )

        state = {"cursor": "cursor-1", "sent_batch_ids": ["b1"], "recent_reply_times": []}
        listener.poll_once(state)

        self.assertEqual(len(sent), 1)


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

    def test_boot_message_batch_id_added_to_sent_ids(self):
        listener.teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (True, "發送成功", "real-batch-id-123")
        )
        listener.teamplus_api.read_new_messages = lambda cursor: self.fail("不該呼叫")

        state = listener.init_listener_state()
        self.assertIn("real-batch-id-123", state["sent_batch_ids"])

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
