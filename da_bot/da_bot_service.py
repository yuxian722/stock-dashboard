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

原本「這個小時推播過了沒」只記在記憶體變數(last_pipeline_hour)裡，
每次重新啟動服務都會歸零成None，導致同一小時內只要重開程式，就會立刻
重新觸發一次整點任務(重抓CPIS資料+重推播一次)，跟有沒有真的到整點無關。
偵錯期間反覆重開測試的話，team+群組裡就會看到一堆時間相近、數字微調的
重複推播訊息(看起來像陷入迴圈，其實是「重啟次數=重複推播次數」)。
改成把「這個小時推播過了沒」存到硬碟上的小檔案(LAST_PIPELINE_HOUR_PATH)，
重啟服務時先讀這個檔案，如果這小時已經推播過了就不會再立刻重推，
會乖乖等到下一個真正的整點。
"""
import os
import sys

# Windows主控台預設用cp950(繁體中文)編碼，推播/回覆內容含emoji(🔧⏳⚡🤖等)
# 沒辦法用cp950編碼，print()會直接丟UnicodeEncodeError把這支長駐服務弄當掉。
# 改成把stdout/stderr強制用utf-8輸出，encode不了的字元用errors="replace"跳過。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import threading
import time
import datetime

import teamplus_listener as listener
import run_pipeline
import singleton_lock

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_PIPELINE_HOUR_PATH = os.path.join(SCRIPT_DIR, "last_pipeline_hour.txt")


def _load_last_pipeline_hour():
    """讀取上次(不管服務有沒有重開過)已經觸發過整點任務的小時(YYYYMMDDHH)，沒有紀錄回傳None。"""
    try:
        with open(LAST_PIPELINE_HOUR_PATH, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def _save_last_pipeline_hour(hour_key):
    with open(LAST_PIPELINE_HOUR_PATH, "w", encoding="utf-8") as f:
        f.write(hour_key)


def _run_pipeline_in_background(hour_key):
    print(f"[整點觸發] {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
          f"開始在背景執行資料更新+推播(hour={hour_key})...")
    try:
        run_pipeline.run_once()
    except Exception as e:
        print(f"[警告] 整點任務發生例外，本次跳過，下個整點再試: {type(e).__name__}: {e}")


def main():
    # 確保同時間只有一個process在監聽team+訊息(不管是這支合併服務還是單獨的
    # teamplus_listener.py)，避免兩個process互相把對方的回覆當成新指令、
    # 無限自問自答(2026/08/10使用者回報的DB改機無限迴圈，見singleton_lock.py)
    singleton_lock.acquire_or_exit()

    state = listener.init_listener_state()

    # 記錄上次「觸發」整點任務是哪個小時(格式YYYYMMDDHH)，避免同一小時內重複觸發
    # (改讀硬碟上的紀錄檔，而不是每次重開都歸零成None，避免重開服務=重推播)
    last_pipeline_hour = _load_last_pipeline_hour()
    pipeline_thread = None

    print(f"[服務啟動] 每 {listener.POLL_INTERVAL_SECONDS} 秒檢查即時訊息、每整點自動更新+推播(背景執行緒)，Ctrl+C 結束")

    while True:
        time.sleep(listener.POLL_INTERVAL_SECONDS)

        now = datetime.datetime.now()
        current_hour_key = now.strftime("%Y%m%d%H")

        # ---------- 整點任務(丟到背景執行緒，不擋住下面的即時問答) ----------
        if last_pipeline_hour != current_hour_key and (pipeline_thread is None or not pipeline_thread.is_alive()):
            last_pipeline_hour = current_hour_key
            _save_last_pipeline_hour(current_hour_key)
            pipeline_thread = threading.Thread(
                target=_run_pipeline_in_background, args=(current_hour_key,), daemon=True
            )
            pipeline_thread.start()

        # ---------- 即時問答任務(原teamplus_listener.py，走HTTP API) ----------
        # 不管整點任務有沒有在背景跑，這裡每一輪(10秒)都要照樣執行
        listener.poll_once(state)


if __name__ == "__main__":
    main()
