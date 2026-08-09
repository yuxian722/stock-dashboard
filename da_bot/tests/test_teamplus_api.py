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
        texts, cursor = teamplus_api.read_new_messages(None)
        self.assertEqual(texts, ["BAA08"])
        self.assertEqual(cursor, "b1")

    def test_message_list_key_fallback(self):
        self._fake_urlopen_returning({
            "MessageList": [{"MsgContent": "BAA08", "BatchID": "b1"}]
        })
        texts, cursor = teamplus_api.read_new_messages(None)
        self.assertEqual(texts, ["BAA08"])
        self.assertEqual(cursor, "b1")

    def test_chat_message_list_takes_priority_when_both_present(self):
        self._fake_urlopen_returning({
            "ChatMessageList": [{"MsgContent": "來自ChatMessageList", "BatchID": "b1"}],
            "MessageList": [{"MsgContent": "來自MessageList", "BatchID": "b2"}],
        })
        texts, _ = teamplus_api.read_new_messages(None)
        self.assertEqual(texts, ["來自ChatMessageList"])

    def test_neither_key_present_returns_empty_without_error(self):
        self._fake_urlopen_returning({"SomethingElse": []})
        texts, cursor = teamplus_api.read_new_messages("old-cursor")
        self.assertEqual(texts, [])
        self.assertEqual(cursor, "old-cursor")


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
