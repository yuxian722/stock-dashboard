"""da_bot_service.py 的離線單元測試(不連網)：整點任務背景執行緒的包裝函式。

之前main()裡是同步呼叫run_pipeline.run_once()，整點任務最壞情況(三個步驟
各900秒逾時上限)可以卡住主迴圈快半小時，這段時間listener.poll_once()完全
不會被呼叫，team+訊息不會有任何回覆(看起來像機器人完全沒反應)。改成丟到
背景執行緒後，這裡鎖定_run_pipeline_in_background()本身不會讓例外往外噴出
(否則會讓執行緒直接死掉、之後的整點都不會再觸發)。
"""

import conftest  # noqa: F401  (設定 sys.path)

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


if __name__ == "__main__":
    unittest.main()
