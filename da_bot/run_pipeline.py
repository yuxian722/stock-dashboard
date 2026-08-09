"""
DA 監控機器人 - 排程用主控腳本
用途：計算日期範圍(昨天~今天)，依序執行 cpis_scraper.py(更新資料) 和
     teamplus_push.py(自動推播)，供 Windows工作排程器 呼叫。

使用前務必確認：da_bot資料夾下有 config.txt(CPIS/APG帳密，見config.txt.example)
跟 teamplus_cookie.txt(team+推播用)。CPIS跟team+都已改用HTTP API(cpis_api.py/
teamplus_api.py)，不再需要開附身模式Edge。建議先手動測試這支腳本能跑通，
再設定排程。
"""
import subprocess
import sys
import datetime
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENTITY_PATTERN = "BA*"  # 依使用者偏好，固定用有範圍的萬用字元，避免對CPIS系統造成過大負擔
# 注意：CPIS的txtentity欄位規定扣掉萬用字元後至少要有2個字元，"B*"實測會被
# CPIS前端擋掉(彈alert、查不到任何資料)，改用"BA*"才符合規則。

LOG_PATH = os.path.join(SCRIPT_DIR, "push_log.txt")

# 單一步驟最長等待秒數(08/06調整：原本600秒，cpis_utilization_scraper.py改版後
# 光是等表格載入穩定+途中若遇session過期重試，最壞情況就逼近600秒上限，
# 排程半夜自動跑、沒人在旁邊即時處理的情況下風險較高，拉寬到900秒留緩衝)
STEP_TIMEOUT_SECONDS = 900


def log(msg: str):
    line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_step(args, step_name: str, timeout: int = STEP_TIMEOUT_SECONDS) -> bool:
    log(f"[開始] {step_name}: {' '.join(args)}")
    try:
        result = subprocess.run(
            [sys.executable] + args,
            cwd=SCRIPT_DIR,
            capture_output=True, text=True, timeout=timeout
        )
        log(result.stdout)
        if result.stderr:
            log(f"[stderr] {result.stderr}")
        if result.returncode != 0:
            log(f"[失敗] {step_name} 回傳碼 {result.returncode}")
            return False
        log(f"[完成] {step_name}")
        return True
    except subprocess.TimeoutExpired:
        log(f"[錯誤] {step_name} 執行超過{timeout}秒，強制中止")
        return False
    except Exception as e:
        log(f"[錯誤] {step_name} 發生例外: {e}")
        return False


def run_once():
    """
    執行一次完整流程：更新維修資料 → 更新稼動率資料 → 推播到team+。
    抽成函式讓da_bot_service.py可以在自己的迴圈裡直接呼叫，
    不用像Windows工作排程器那樣另外開一個subprocess執行整支腳本。
    獨立執行 python run_pipeline.py 時效果不變，只是改成呼叫這個函式。
    """
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    date_start = yesterday.strftime("%Y%m%d")
    date_end = today.strftime("%Y%m%d")
    today_str = today.strftime("%Y%m%d")

    log("=" * 40)
    log(f"排程執行開始，查詢區間 {date_start}~{date_end}，Entity={ENTITY_PATTERN}")

    ok1 = run_step(
        ["cpis_scraper.py", date_start, date_end, ENTITY_PATTERN],
        "更新CPIS維修資料(EE Maintenance Record)"
    )
    if not ok1:
        log("[中止] 維修資料更新失敗，跳過推播避免推送舊資料")
        return False

    # Utilization Analysis(稼動率)這個資料源獨立失敗不中止整體流程，
    # 因為就算抓不到rates，修機/改機的整點提醒還是該正常推播出去
    ok_util = run_step(
        ["cpis_utilization_scraper.py", today_str, today_str],
        "更新CPIS稼動率資料(Utilization Analysis)"
    )
    if not ok_util:
        log("[警告] 稼動率資料更新失敗，訊息裡的rates會顯示暫無，繼續往下推播")

    ok2 = run_step(["teamplus_push.py"], "推播到team+")
    if not ok2:
        log("[失敗] 推播失敗")
        return False

    log("排程執行全部完成")
    return True


if __name__ == "__main__":
    ok = run_once()
    sys.exit(0 if ok else 1)
