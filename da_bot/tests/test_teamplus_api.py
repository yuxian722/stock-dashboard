"""teamplus_api.py 的離線單元測試(不連網)：額外聊天室清單解析、broadcast_message
依序呼叫send_message送到每個聊天室(用假的send_message攔截，不會真的發HTTP請求)、
以及read_new_messages()對回傳JSON裡訊息清單欄位名稱的容錯處理。"""

import conftest  # noqa: F401  (設定 sys.path)

import json
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
    def setUp(self):
        self._orig_send = teamplus_api.send_message
        self._orig_extra = teamplus_api._load_extra_chat_ids

    def tearDown(self):
        teamplus_api.send_message = self._orig_send
        teamplus_api._load_extra_chat_ids = self._orig_extra

    def test_sends_to_default_room_when_no_extra_configured(self):
        teamplus_api._load_extra_chat_ids = lambda: []
        calls = []

        def fake_send(message, chat_id=None):
            calls.append(chat_id)
            return True, "ok"

        teamplus_api.send_message = fake_send
        results = teamplus_api.broadcast_message("hello")
        self.assertEqual(calls, [teamplus_api.CHAT_ID])
        self.assertEqual(results, [(teamplus_api.CHAT_ID, True, "ok")])

    def test_sends_to_default_and_extra_rooms(self):
        teamplus_api._load_extra_chat_ids = lambda: ["room-a", "room-b"]
        calls = []

        def fake_send(message, chat_id=None):
            calls.append(chat_id)
            return True, "ok"

        teamplus_api.send_message = fake_send
        results = teamplus_api.broadcast_message("hello")
        self.assertEqual(calls, [teamplus_api.CHAT_ID, "room-a", "room-b"])
        self.assertEqual(len(results), 3)

    def test_reports_individual_room_failures(self):
        teamplus_api._load_extra_chat_ids = lambda: ["room-a"]

        def fake_send(message, chat_id=None):
            if chat_id == "room-a":
                return False, "cookie過期"
            return True, "ok"

        teamplus_api.send_message = fake_send
        results = teamplus_api.broadcast_message("hello")
        self.assertEqual(
            results,
            [(teamplus_api.CHAT_ID, True, "ok"), ("room-a", False, "cookie過期")],
        )


if __name__ == "__main__":
    unittest.main()
