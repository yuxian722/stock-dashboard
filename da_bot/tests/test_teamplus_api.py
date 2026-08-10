"""teamplus_api.py 的離線單元測試(不連網)：額外聊天室清單解析、broadcast_message
依序呼叫send_message送到每個聊天室(用假的send_message攔截，不會真的發HTTP請求)、
以及read_new_messages()對回傳JSON裡訊息清單欄位名稱的容錯處理。"""

import conftest  # noqa: F401  (設定 sys.path)

import json
import tempfile
import unittest

import teamplus_api


class _FakeResponse:
    """假的urllib.request.urlopen()回傳物件，模擬with語法+read()。"""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class TestReadNewMessagesFieldNameFallback(unittest.TestCase):
    """team+這支API實際回傳的清單欄位名稱有時是ChatMessageList、有時是MessageList
    (同事逆向出來的teamplus_bot.py兩個都有處理)。只認其中一個名稱的話，
    剛好遇到另一個名稱時就會每次都誤判成"沒有新訊息"，即時問答會變成永遠
    沒反應、也不會印出任何錯誤或警告——這裡鎖定兩種欄位名稱都要能正確解析。
    """

    def setUp(self):
        self._orig_load_cookie = teamplus_api.load_cookie
        self._orig_urlopen = teamplus_api.urllib.request.urlopen
        teamplus_api.load_cookie = lambda: "fake_cookie=1"

    def tearDown(self):
        teamplus_api.load_cookie = self._orig_load_cookie
        teamplus_api.urllib.request.urlopen = self._orig_urlopen

    def _fake_urlopen_returning(self, payload):
        def fake_urlopen(req, context=None, timeout=None):
            return _FakeResponse(payload)
        teamplus_api.urllib.request.urlopen = fake_urlopen

    def test_chat_message_list_key(self):
        self._fake_urlopen_returning({
            "ChatMessageList": [{"MsgContent": "BAA08", "BatchID": "b1"}]
        })
        messages, cursor = teamplus_api.read_new_messages(None)
        self.assertEqual(messages, [{"text": "BAA08", "batch_id": "b1"}])
        self.assertEqual(cursor, "b1")

    def test_message_list_key_fallback(self):
        self._fake_urlopen_returning({
            "MessageList": [{"MsgContent": "BAA08", "BatchID": "b1"}]
        })
        messages, cursor = teamplus_api.read_new_messages(None)
        self.assertEqual(messages, [{"text": "BAA08", "batch_id": "b1"}])
        self.assertEqual(cursor, "b1")

    def test_chat_message_list_takes_priority_when_both_present(self):
        self._fake_urlopen_returning({
            "ChatMessageList": [{"MsgContent": "來自ChatMessageList", "BatchID": "b1"}],
            "MessageList": [{"MsgContent": "來自MessageList", "BatchID": "b2"}],
        })
        messages, _ = teamplus_api.read_new_messages(None)
        self.assertEqual([m["text"] for m in messages], ["來自ChatMessageList"])

    def test_neither_key_present_returns_empty_without_error(self):
        self._fake_urlopen_returning({"SomethingElse": []})
        messages, cursor = teamplus_api.read_new_messages("old-cursor")
        self.assertEqual(messages, [])
        self.assertEqual(cursor, "old-cursor")


class TestReadNewMessagesCursorBootstrap(unittest.TestCase):
    """team+的getNewestMessageList這支API，NewestBatchID傳空字串會直接回
    IsSuccess=false"參數錯誤：NewestBatchID"——這是之前即時問答從頭到尾完全
    沒反應的真正原因：cursor第一次是None，過去的寫法會送出空字串，永遠卡在
    這個參數錯誤，state["cursor"]永遠初始化不了。改成cursor是None時自動帶一個
    隨機UUID，這裡鎖定：(1)真的送出去的NewestBatchID不能是空字串，
    (2)就算這次沒有新訊息，回傳的cursor也要是這次實際用的UUID(不能是None)，
    不然下一輪又會重新產生一個新的UUID，永遠沒辦法穩定用同一個cursor追蹤下去。
    """

    def setUp(self):
        self._orig_load_cookie = teamplus_api.load_cookie
        self._orig_urlopen = teamplus_api.urllib.request.urlopen
        teamplus_api.load_cookie = lambda: "fake_cookie=1"

    def tearDown(self):
        teamplus_api.load_cookie = self._orig_load_cookie
        teamplus_api.urllib.request.urlopen = self._orig_urlopen

    def test_none_cursor_never_sends_empty_newest_batch_id(self):
        captured = {}

        def fake_urlopen(req, context=None, timeout=None):
            body = req.data.decode("utf-8")
            captured["NewestBatchID"] = teamplus_api.urllib.parse.parse_qs(body)["NewestBatchID"][0]
            return _FakeResponse({"IsSuccess": True, "MessageList": [], "Description": "查無資料"})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        teamplus_api.read_new_messages(None)
        self.assertNotEqual(captured["NewestBatchID"], "")

    def test_bootstrap_with_no_new_messages_returns_nonempty_cursor(self):
        def fake_urlopen(req, context=None, timeout=None):
            return _FakeResponse({"IsSuccess": True, "MessageList": [], "Description": "查無資料"})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        messages, cursor = teamplus_api.read_new_messages(None)
        self.assertEqual(messages, [])
        self.assertTrue(cursor)  # 不能是None、也不能是空字串

    def test_bootstrap_cursor_reused_on_next_call_with_no_new_messages(self):
        # 承上，重點是：下一輪呼叫要能拿這個回傳的cursor繼續用，
        # 不會又送出空字串重蹈覆轍
        def fake_urlopen(req, context=None, timeout=None):
            return _FakeResponse({"IsSuccess": True, "MessageList": [], "Description": "查無資料"})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        _, cursor1 = teamplus_api.read_new_messages(None)

        captured = {}

        def fake_urlopen2(req, context=None, timeout=None):
            body = req.data.decode("utf-8")
            captured["NewestBatchID"] = teamplus_api.urllib.parse.parse_qs(body)["NewestBatchID"][0]
            return _FakeResponse({"IsSuccess": True, "MessageList": [], "Description": "查無資料"})

        teamplus_api.urllib.request.urlopen = fake_urlopen2
        teamplus_api.read_new_messages(cursor1)
        self.assertEqual(captured["NewestBatchID"], cursor1)

    def test_chat_id_param_used_when_given(self):
        # 2026/08/10使用者要求即時問答支援額外聊天室：read_new_messages()
        # 要能指定要讀哪一間聊天室，不指定時維持原本讀CHAT_ID的行為
        captured = {}

        def fake_urlopen(req, context=None, timeout=None):
            body = req.data.decode("utf-8")
            captured["ChatID"] = teamplus_api.urllib.parse.parse_qs(body)["ChatID"][0]
            return _FakeResponse({"IsSuccess": True, "MessageList": [], "Description": "查無資料"})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        teamplus_api.read_new_messages(None, chat_id="room-a")
        self.assertEqual(captured["ChatID"], "room-a")

    def test_defaults_to_chat_id_constant_when_not_given(self):
        captured = {}

        def fake_urlopen(req, context=None, timeout=None):
            body = req.data.decode("utf-8")
            captured["ChatID"] = teamplus_api.urllib.parse.parse_qs(body)["ChatID"][0]
            return _FakeResponse({"IsSuccess": True, "MessageList": [], "Description": "查無資料"})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        teamplus_api.read_new_messages(None)
        self.assertEqual(captured["ChatID"], teamplus_api.CHAT_ID)

    def test_p2p_chat_id_sends_channel_type_zero(self):
        # 2026/08/10使用者實測發現：跟同事的1對1對話(ChatID格式"我的Mobile_
        # 對方Mobile")原本用固定的群組ChannelType=1會讀不到訊息，要自動判斷
        # 換成ChannelType=0
        captured = {}

        def fake_urlopen(req, context=None, timeout=None):
            body = req.data.decode("utf-8")
            captured["ChannelType"] = teamplus_api.urllib.parse.parse_qs(body)["ChannelType"][0]
            return _FakeResponse({"IsSuccess": True, "MessageList": [], "Description": "查無資料"})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        teamplus_api.read_new_messages(None, chat_id=f"{teamplus_api.MOBILE}_1631")
        self.assertEqual(captured["ChannelType"], "0")

    def test_group_chat_id_still_sends_channel_type_one(self):
        captured = {}

        def fake_urlopen(req, context=None, timeout=None):
            body = req.data.decode("utf-8")
            captured["ChannelType"] = teamplus_api.urllib.parse.parse_qs(body)["ChannelType"][0]
            return _FakeResponse({"IsSuccess": True, "MessageList": [], "Description": "查無資料"})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        teamplus_api.read_new_messages(None, chat_id="F505A503-B077-489C-B596-AF9C52FF6224")
        self.assertEqual(captured["ChannelType"], "1")


class TestChannelInfoForChat(unittest.TestCase):
    """_channel_info_for_chat()：依ChatID格式自動判斷群組(ChannelType=1)
    還是1對1個人對話(ChannelType=0，Recipients換成對方Mobile)。"""

    def test_p2p_format_matching_own_mobile_detected(self):
        channel_type, recipients = teamplus_api._channel_info_for_chat(f"{teamplus_api.MOBILE}_1631")
        self.assertEqual(channel_type, "0")
        self.assertEqual(recipients, [{"Mobile": "1631", "Email": ""}])

    def test_guid_chat_id_treated_as_group(self):
        channel_type, recipients = teamplus_api._channel_info_for_chat(
            "F505A503-B077-489C-B596-AF9C52FF6224"
        )
        self.assertEqual(channel_type, teamplus_api.CHANNEL_TYPE)
        self.assertEqual(recipients, teamplus_api.RECIPIENTS)

    def test_underscore_format_not_matching_own_mobile_treated_as_group(self):
        # 前半段不是自己的Mobile，不符合P2P的假設，安全起見當群組處理
        channel_type, recipients = teamplus_api._channel_info_for_chat("1631_903")
        self.assertEqual(channel_type, teamplus_api.CHANNEL_TYPE)

    def test_none_or_empty_treated_as_group(self):
        channel_type, recipients = teamplus_api._channel_info_for_chat(None)
        self.assertEqual(channel_type, teamplus_api.CHANNEL_TYPE)
        channel_type, recipients = teamplus_api._channel_info_for_chat("")
        self.assertEqual(channel_type, teamplus_api.CHANNEL_TYPE)


class TestSendMessageGetBatchId(unittest.TestCase):
    """同事的teamplus_bot.py開機時靠「送一則上線通知、拿這則訊息真正的
    batchID當cursor」來啟動監聽，比讀空cursor可靠(那條路線被team+直接
    拒絕、參數錯誤)。這裡鎖定send_message_get_batch_id()回傳的batch_id
    就是實際送出去那筆請求裡真正用的batchID，呼叫端才能拿去當cursor用。
    """

    def setUp(self):
        self._orig_load_cookie = teamplus_api.load_cookie
        self._orig_urlopen = teamplus_api.urllib.request.urlopen
        teamplus_api.load_cookie = lambda: "fake_cookie=1"

    def tearDown(self):
        teamplus_api.load_cookie = self._orig_load_cookie
        teamplus_api.urllib.request.urlopen = self._orig_urlopen

    def test_returned_batch_id_matches_what_was_actually_sent(self):
        captured = {}

        def fake_urlopen(req, context=None, timeout=None):
            body = req.data.decode("utf-8")
            captured["batchID"] = teamplus_api.urllib.parse.parse_qs(body)["batchID"][0]
            return _FakeResponse({"IsSuccess": True})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        ok, desc, bid = teamplus_api.send_message_get_batch_id("上線通知")
        self.assertTrue(ok)
        self.assertEqual(bid, captured["batchID"])

    def test_failure_still_returns_the_batch_id_that_was_attempted(self):
        def fake_urlopen(req, context=None, timeout=None):
            return _FakeResponse({"IsSuccess": False, "Description": "cookie過期"})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        ok, desc, bid = teamplus_api.send_message_get_batch_id("上線通知")
        self.assertFalse(ok)
        self.assertTrue(bid)

    def test_p2p_chat_id_sends_other_partys_mobile_as_recipient(self):
        # 2026/08/10使用者實測發現：P2P對話送出去的Recipients要帶對方的
        # Mobile，不是固定帶自己的903，不然訊息送不出去/對方收不到
        captured = {}

        def fake_urlopen(req, context=None, timeout=None):
            body = req.data.decode("utf-8")
            captured["Recipients"] = teamplus_api.urllib.parse.parse_qs(body)["Recipients"][0]
            captured["ChannelType"] = teamplus_api.urllib.parse.parse_qs(body)["ChannelType"][0]
            return _FakeResponse({"IsSuccess": True})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        teamplus_api.send_message_get_batch_id("測試訊息", chat_id=f"{teamplus_api.MOBILE}_1631")
        self.assertEqual(captured["ChannelType"], "0")
        self.assertIn('"Mobile":"1631"', captured["Recipients"])

    def test_group_chat_id_still_sends_own_mobile_as_recipient(self):
        captured = {}

        def fake_urlopen(req, context=None, timeout=None):
            body = req.data.decode("utf-8")
            captured["Recipients"] = teamplus_api.urllib.parse.parse_qs(body)["Recipients"][0]
            return _FakeResponse({"IsSuccess": True})

        teamplus_api.urllib.request.urlopen = fake_urlopen
        teamplus_api.send_message_get_batch_id("測試訊息", chat_id="F505A503-B077-489C-B596-AF9C52FF6224")
        self.assertIn(f'"Mobile":"{teamplus_api.MOBILE}"', captured["Recipients"])


class TestLoadExtraChatIds(unittest.TestCase):
    def setUp(self):
        self._orig_load = teamplus_api.config.load

    def tearDown(self):
        teamplus_api.config.load = self._orig_load

    def test_missing_config_returns_empty(self):
        def fake_load(path=None):
            raise FileNotFoundError("no config.txt")
        teamplus_api.config.load = fake_load
        self.assertEqual(teamplus_api._load_extra_chat_ids(), [])

    def test_no_key_returns_empty(self):
        teamplus_api.config.load = lambda path=None: {}
        self.assertEqual(teamplus_api._load_extra_chat_ids(), [])

    def test_parses_comma_separated_list_and_strips_whitespace(self):
        teamplus_api.config.load = lambda path=None: {"teamplus_extra_chat_ids": " room-a , room-b ,"}
        self.assertEqual(teamplus_api._load_extra_chat_ids(), ["room-a", "room-b"])


class TestAllChatIds(unittest.TestCase):
    """all_chat_ids()：CHAT_ID+額外聊天室(去重、保留順序)，
    broadcast_message()推播跟teamplus_listener.py的即時問答監聽
    (2026/08/10使用者要求Q&A也要支援額外聊天室)共用這份清單。"""

    def setUp(self):
        self._orig_extra = teamplus_api._load_extra_chat_ids

    def tearDown(self):
        teamplus_api._load_extra_chat_ids = self._orig_extra

    def test_returns_only_chat_id_when_no_extra_configured(self):
        teamplus_api._load_extra_chat_ids = lambda: []
        self.assertEqual(teamplus_api.all_chat_ids(), [teamplus_api.CHAT_ID])

    def test_appends_extra_rooms_in_order(self):
        teamplus_api._load_extra_chat_ids = lambda: ["room-a", "room-b"]
        self.assertEqual(teamplus_api.all_chat_ids(), [teamplus_api.CHAT_ID, "room-a", "room-b"])

    def test_deduplicates_if_chat_id_repeated_in_extra_list(self):
        teamplus_api._load_extra_chat_ids = lambda: [teamplus_api.CHAT_ID, "room-a"]
        self.assertEqual(teamplus_api.all_chat_ids(), [teamplus_api.CHAT_ID, "room-a"])


class TestBroadcastMessage(unittest.TestCase):
    """broadcast_message()改用send_message_get_batch_id()(不是send_message())，
    這樣才能拿到batchID記進recent_self_sent_batch_ids()共用檔案，讓
    (通常是不同process的)即時問答監聽認得出這是自己推播送出的訊息
    (2026/08/10使用者實測發現：整點推播內容剛好含有查詢關鍵字，被監聽端
    誤判成新指令、多回了一則報告)。"""

    def setUp(self):
        self._orig_send_bid = teamplus_api.send_message_get_batch_id
        self._orig_extra = teamplus_api._load_extra_chat_ids
        self._orig_log_path = teamplus_api.SELF_SENT_LOG_PATH
        teamplus_api.SELF_SENT_LOG_PATH = tempfile.mktemp(suffix=".log")

    def tearDown(self):
        teamplus_api.send_message_get_batch_id = self._orig_send_bid
        teamplus_api._load_extra_chat_ids = self._orig_extra
        teamplus_api.SELF_SENT_LOG_PATH = self._orig_log_path

    def test_sends_to_default_room_when_no_extra_configured(self):
        teamplus_api._load_extra_chat_ids = lambda: []
        calls = []

        def fake_send_bid(message, chat_id=None):
            calls.append(chat_id)
            return True, "ok", f"bid-{chat_id}"

        teamplus_api.send_message_get_batch_id = fake_send_bid
        results = teamplus_api.broadcast_message("hello")
        self.assertEqual(calls, [teamplus_api.CHAT_ID])
        self.assertEqual(results, [(teamplus_api.CHAT_ID, True, "ok")])

    def test_sends_to_default_and_extra_rooms(self):
        teamplus_api._load_extra_chat_ids = lambda: ["room-a", "room-b"]
        calls = []

        def fake_send_bid(message, chat_id=None):
            calls.append(chat_id)
            return True, "ok", f"bid-{chat_id}"

        teamplus_api.send_message_get_batch_id = fake_send_bid
        results = teamplus_api.broadcast_message("hello")
        self.assertEqual(calls, [teamplus_api.CHAT_ID, "room-a", "room-b"])
        self.assertEqual(len(results), 3)

    def test_reports_individual_room_failures(self):
        teamplus_api._load_extra_chat_ids = lambda: ["room-a"]

        def fake_send_bid(message, chat_id=None):
            if chat_id == "room-a":
                return False, "cookie過期", "unused-bid"
            return True, "ok", f"bid-{chat_id}"

        teamplus_api.send_message_get_batch_id = fake_send_bid
        results = teamplus_api.broadcast_message("hello")
        self.assertEqual(
            results,
            [(teamplus_api.CHAT_ID, True, "ok"), ("room-a", False, "cookie過期")],
        )

    def test_successful_sends_recorded_for_cross_process_self_answer_check(self):
        teamplus_api._load_extra_chat_ids = lambda: []
        teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (True, "ok", "push-bid-123")
        )
        teamplus_api.broadcast_message("整點推播內容")
        self.assertIn("push-bid-123", teamplus_api.recent_self_sent_batch_ids())

    def test_failed_send_not_recorded(self):
        teamplus_api._load_extra_chat_ids = lambda: []
        teamplus_api.send_message_get_batch_id = (
            lambda message, chat_id=None: (False, "cookie過期", "unused-bid")
        )
        teamplus_api.broadcast_message("整點推播內容")
        self.assertNotIn("unused-bid", teamplus_api.recent_self_sent_batch_ids())


class TestSelfSentBatchIdLog(unittest.TestCase):
    """_record_self_sent_batch_id()/recent_self_sent_batch_ids()：跨process
    共用的「自己送出過的訊息BatchID」記錄，給即時問答監聽認出整點推播
    (獨立process)送出的訊息，避免誤判成新指令。"""

    def setUp(self):
        self._orig_log_path = teamplus_api.SELF_SENT_LOG_PATH
        teamplus_api.SELF_SENT_LOG_PATH = tempfile.mktemp(suffix=".log")

    def tearDown(self):
        teamplus_api.SELF_SENT_LOG_PATH = self._orig_log_path

    def test_missing_file_returns_empty_set(self):
        self.assertEqual(teamplus_api.recent_self_sent_batch_ids(), set())

    def test_records_and_reads_back(self):
        teamplus_api._record_self_sent_batch_id("bid-1")
        teamplus_api._record_self_sent_batch_id("bid-2")
        self.assertEqual(teamplus_api.recent_self_sent_batch_ids(), {"bid-1", "bid-2"})

    def test_trims_to_keep_limit(self):
        for i in range(teamplus_api._SELF_SENT_LOG_KEEP + 10):
            teamplus_api._record_self_sent_batch_id(f"bid-{i}")
        recorded = teamplus_api.recent_self_sent_batch_ids()
        self.assertEqual(len(recorded), teamplus_api._SELF_SENT_LOG_KEEP)
        # 最新的那筆一定還在，最舊的那幾筆應該已經被修剪掉
        self.assertIn(f"bid-{teamplus_api._SELF_SENT_LOG_KEEP + 9}", recorded)
        self.assertNotIn("bid-0", recorded)


if __name__ == "__main__":
    unittest.main()
