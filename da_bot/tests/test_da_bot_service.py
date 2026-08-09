"""da_bot_service.py 的離線單元測試(不連網)：整點任務背景執行緒的包裝函式，
以及「這個小時推播過了沒」的硬碟持久化紀錄。

之前main()裡是同步呼叫run_pipeline.run_once()，整點任務最壞情況(三個步驟
各900秒逾時上限)可以卡住主迴圈快半小時，這段時間listener.poll_once()完全
不會被呼叫，team+訊息不會有任何回覆(看起來像機器人完全沒反應)。改成丟到
背景執行緒後，這裡鎖定_run_pipeline_in_background()本身不會讓例外往外噴出
(否則會讓執行緒直接死掉、之後的整點都不會再觸發)。

另外，「這個小時觸發過整點任務了沒」原本只記在記憶體變數裡，重開服務就會
歸零成None，導致同一小時內只要重開程式就會立刻重新觸發一次(重抓資料+
重推播一次)，偵錯期間反覆重開測試會在team+群組裡洗出一堆重複推播，
看起來像陷入迴圈。這裡鎖定改成寫到硬碟檔案後，讀回來的值要跟寫進去的一樣。
"""

import conftest  # noqa: F401  (設定 sys.path)

import os
import tempfile
import unittest

import da_bot_service


class TestRunPipelineInBackground(unittest.TestCase):
    def setUp(self):
        self._orig_run_once = da_bot_service.run_pipeline.run_once

    def tearDown(self):
        da_bot_service.run_pipeline.run_once = self._orig_run_once

    def test_exception_does_not_propagate(self):
        def boom():
            raise RuntimeError("CPIS連線失敗")

        da_bot_service.run_pipeline.run_once = boom
        try:
            da_bot_service._run_pipeline_in_background("2026080913")
        except Exception:
            self.fail("_run_pipeline_in_background() 不應該讓例外往外噴出(會讓背景執行緒直接死掉)")

    def test_success_calls_run_once(self):
        calls = []
        da_bot_service.run_pipeline.run_once = lambda: calls.append(1)
        da_bot_service._run_pipeline_in_background("2026080913")
        self.assertEqual(calls, [1])


class TestLastPipelineHourPersistence(unittest.TestCase):
    def setUp(self):
        self._orig_path = da_bot_service.LAST_PIPELINE_HOUR_PATH
        da_bot_service.LAST_PIPELINE_HOUR_PATH = tempfile.mktemp(suffix=".txt")

    def tearDown(self):
        try:
            os.remove(da_bot_service.LAST_PIPELINE_HOUR_PATH)
        except FileNotFoundError:
            pass
        da_bot_service.LAST_PIPELINE_HOUR_PATH = self._orig_path

    def test_no_file_yet_returns_none(self):
        self.assertIsNone(da_bot_service._load_last_pipeline_hour())

    def test_round_trip(self):
        da_bot_service._save_last_pipeline_hour("2026080914")
        self.assertEqual(da_bot_service._load_last_pipeline_hour(), "2026080914")

    def test_restart_within_same_hour_does_not_retrigger(self):
        # 模擬：這個小時已經推播過、寫進檔案了，服務重開後從檔案讀回來的值
        # 要跟目前小時相等，不能變成None(否則主迴圈會誤判成沒推播過又重推一次)
        da_bot_service._save_last_pipeline_hour("2026080914")
        reloaded = da_bot_service._load_last_pipeline_hour()
        self.assertEqual(reloaded, "2026080914")
        self.assertNotEqual(reloaded, None)


if __name__ == "__main__":
    unittest.main()
