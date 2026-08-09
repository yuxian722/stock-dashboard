"""teamplus_api.py 的離線單元測試(不連網)：額外聊天室清單解析、broadcast_message
依序呼叫send_message送到每個聊天室(用假的send_message攔截，不會真的發HTTP請求)。"""

import conftest  # noqa: F401  (設定 sys.path)

import unittest

import teamplus_api


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
