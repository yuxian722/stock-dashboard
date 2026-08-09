"""
DA 監控機器人 - 合併常駐服務 (CPIS改版：整點任務也不再需要Edge/Selenium)

同時做兩件事：
1. 每10秒檢查team+「機器人推播」室有沒有新訊息，即時問答回覆
   (透過teamplus_api.py的HTTP API，不需要Edge/Selenium)
2. 每整點自動執行一次CPIS資料更新+推播(呼叫run_pipeline.py的run_once())

════════════════════════════════════════
改版說明
════════════════════════════════════════
即時問答(第1件事)跟整點任務裡的CPIS資料更新(第2件事，run_pipeline.run_once()
呼叫的cpis_scraper.py/cpis_utilization_scraper.py)都已經改用HTTP API
(teamplus_api.py / cpis_api.py)，不再需要附身模式Edge，這支服務完全不用
管理/持有Selenium driver。

用法：
    python da_bot_service.py
    Ctrl+C 結束

前置：
- da_bot資料夾下有 teamplus_cookie.txt(即時問答部分要用)
- da_bot資料夾下有 config.txt(整點任務的CPIS資料更新要用，複製
  config.txt.example改名，填入apg_user/apg_password/util_user/util_password)

════════════════════════════════════════
重要bug修正記錄(08/09)
════════════════════════════════════════
原本整點任務(run_pipeline.run_once())是在主迴圈裡「同步」呼叫，裡面三個步驟
各自subprocess.run()、逾時上限900秒——最壞情況整點任務可以卡住主迴圈快半小時。
卡住的這段時間裡，即時問答的listener.poll_once()完全不會被呼叫，這段時間
使用者在team+打的任何訊息都要等整點任務跑完才會被讀到、回覆(甚至看起來像
機器人完全沒反應)。改成把run_pipeline.run_once()丟到背景執行緒(thread)跑，
主迴圈永遠每10秒檢查一次team+訊息，不會被整點任務卡住。
"""
import threading
import time
import datetime

import teamplus_listener as listener
import run_pipeline


def _run_pipeline_in_background(hour_key):
    print(f"[整點觸發] {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
          f"開始在背景執行資料更新+推播(hour={hour_key})...")
    try:
        run_pipeline.run_once()
    except Exception as e:
        print(f"[警告] 整點任務發生例外，本次跳過，下個整點再試: {type(e).__name__}: {e}")


def main():
    state = listener.init_listener_state()

    # 記錄上次「觸發」整點任務是哪個小時(格式YYYYMMDDHH)，避免同一小時內重複觸發
    last_pipeline_hour = None
    pipeline_thread = None

    print(f"[服務啟動] 每 {listener.POLL_INTERVAL_SECONDS} 秒檢查即時訊息、每整點自動更新+推播(背景執行緒)，Ctrl+C 結束")

    while True:
        time.sleep(listener.POLL_INTERVAL_SECONDS)

        now = datetime.datetime.now()
        current_hour_key = now.strftime("%Y%m%d%H")

        # ---------- 整點任務(丟到背景執行緒，不擋住下面的即時問答) ----------
        if last_pipeline_hour != current_hour_key and (pipeline_thread is None or not pipeline_thread.is_alive()):
            last_pipeline_hour = current_hour_key
            pipeline_thread = threading.Thread(
                target=_run_pipeline_in_background, args=(current_hour_key,), daemon=True
            )
            pipeline_thread.start()

        # ---------- 即時問答任務(原teamplus_listener.py，走HTTP API) ----------
        # 不管整點任務有沒有在背景跑，這裡每一輪(10秒)都要照樣執行
        listener.poll_once(state)


if __name__ == "__main__":
    main()
