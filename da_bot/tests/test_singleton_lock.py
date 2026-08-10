"""singleton_lock.py的離線單元測試：防止兩個process同時監聽team+訊息
(2026/08/10使用者回報DB改機無限自問自答，追查最可能是da_bot_service.py
背景服務跟手動另開的teamplus_listener.py同時在跑，互相把對方的回覆當成
新指令觸發)。

只測非Windows(fcntl)這條路徑——這個沙盒環境是Linux，msvcrt那條路徑要在
Windows上才能真正測試，這裡沒辦法涵蓋，但兩條路徑邏輯是對稱的(都是:
開檔案→試著上非阻塞的獨佔鎖→拿不到就印錯誤訊息+sys.exit(1))。
"""

import conftest  # noqa: F401  (設定 sys.path)

import sys
import tempfile
import unittest

import singleton_lock

_IS_POSIX = not sys.platform.startswith("win")


@unittest.skipUnless(_IS_POSIX, "這個沙盒環境測的是fcntl那條路徑，Windows要另外在Windows上驗證msvcrt路徑")
class TestAcquireOrExit(unittest.TestCase):
    def setUp(self):
        self._orig_lock_path = singleton_lock._LOCK_PATH
        self._orig_lock_file = singleton_lock._lock_file
        singleton_lock._LOCK_PATH = tempfile.mktemp(suffix=".lock")
        singleton_lock._lock_file = None

    def tearDown(self):
        if singleton_lock._lock_file is not None:
            singleton_lock._lock_file.close()
        singleton_lock._LOCK_PATH = self._orig_lock_path
        singleton_lock._lock_file = self._orig_lock_file

    def test_acquires_lock_when_nobody_else_holds_it(self):
        # 不應該丟例外/結束process
        singleton_lock.acquire_or_exit()
        self.assertIsNotNone(singleton_lock._lock_file)

    def test_exits_when_lock_already_held_by_another_file_handle(self):
        # 用另一個獨立的檔案控制代碼模擬「已經有別的process鎖住這個檔案」
        # (fcntl的鎖是綁在file description上，不是process，所以同一個process
        # 開兩個不同的檔案控制代碼一樣可以正確模擬鎖定衝突)
        import fcntl
        holder = open(singleton_lock._LOCK_PATH, "a+")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(SystemExit) as ctx:
                singleton_lock.acquire_or_exit()
            self.assertEqual(ctx.exception.code, 1)
        finally:
            holder.close()

    def test_missing_directory_warns_but_does_not_crash(self):
        # 鎖定檔案的目錄不存在時(理論上不該發生，但要防呆)，印警告訊息、
        # 不應該讓整支服務直接掛掉——寧可失去這層保護也不要連服務都起不來
        singleton_lock._LOCK_PATH = "/no/such/directory/teamplus_listener.lock"
        singleton_lock.acquire_or_exit()  # 不應該丟例外
        self.assertIsNone(singleton_lock._lock_file)


if __name__ == "__main__":
    unittest.main()
