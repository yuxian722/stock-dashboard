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
"""
import time
import datetime

import teamplus_listener as listener
import run_pipeline


def main():
    state = listener.init_listener_state()

    # 記錄上次執行整點任務是哪個小時(格式YYYYMMDDHH)，避免同一小時內重複觸發
    last_pipeline_hour = None

    print(f"[服務啟動] 每 {listener.POLL_INTERVAL_SECONDS} 秒檢查即時訊息、每整點自動更新+推播，Ctrl+C 結束")

    while True:
        time.sleep(listener.POLL_INTERVAL_SECONDS)

        now = datetime.datetime.now()
        current_hour_key = now.strftime("%Y%m%d%H")

        # ---------- 整點任務(原run_pipeline.py，CPIS資料更新已改用HTTP API) ----------
        if last_pipeline_hour != current_hour_key:
            print(f"[整點觸發] {now.strftime('%Y-%m-%d %H:%M:%S')} 開始執行資料更新+推播...")
            try:
                run_pipeline.run_once()
            except Exception as e:
                print(f"[警告] 整點任務發生例外，本次跳過，下個整點再試: {type(e).__name__}: {e}")
            last_pipeline_hour = current_hour_key
            continue  # 這輪先跳過即時問答檢查，下一輪(10秒後)恢復正常監聽

        # ---------- 即時問答任務(原teamplus_listener.py，走HTTP API) ----------
        listener.poll_once(state)


if __name__ == "__main__":
    main()
