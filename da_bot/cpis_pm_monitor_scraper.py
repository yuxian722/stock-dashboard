"""
CPIS「PM/REPAIR/SETUP Monitor」即時機況爬蟲(Selenium無頭瀏覽器版)

這個頁面(wFrmPMRepairSetupMonitor)用Syncfusion Grid元件動態畫表格，
資料是網頁載入後JS另外發背景請求拿回來畫出來的，不是寫死在HTML裡——
跟其他CPIS頁面(EE Maintenance/Utilization Analysis)不一樣，那些後來
都成功改成純HTTP request(cpis_api.py)，但這頁的Grid元件注定得靠瀏覽器
真的執行一次JS、把表格渲染出來，才讀得到資料。診斷過程中試過純urllib
直接GET這個網址、GET裡面的frame網址、遞迴掃iframe，抓到的都只有控制項
(下拉選單/按鈕)，沒有真正的機況資料列，證實了這點。

做法比照同事server.py的headless_capture()：開一個全新的無頭(headless)
Edge，真的把網頁打開、等JS把表格畫出來，再讀渲染後的page_source用
BeautifulSoup解析。這不是本專案之前放棄的「除錯模式附身」(--remote-
debugging-port接到已經開著的瀏覽器，公司網路環境下常常啟動失敗)，
而是每次都全新啟動一個獨立、乾淨的無頭瀏覽器，用完即關，跟同事那套
的做法一樣，穩定性好非常多。

前置：da_bot資料夾下要有 msedgedriver.exe(版本要跟電腦上的Edge相符，
      去 https://developer.microsoft.com/microsoft-edge/tools/webdriver/
      下載)。第一次用可以先跑 requirements_scraper.txt 裝好selenium。

用法：
    import cpis_pm_monitor_scraper
    records = cpis_pm_monitor_scraper.fetch_pm_monitor_records()
    # records: [{"OPER":"DA", "ENTITY":"BA721", "MODEL":"DIE-ATTACH",
    #            "STATUS":"IN-REPAIR", "LOT NO":"...", "Bond ID":"...",
    #            "WIP":"...", "IN TIME":"...", "OUTPLAN":"...",
    #            "JCODE":"...", "OPERATOR":"..."}, ...]

單獨測試(不用寫程式，直接看結果)：
    python cpis_pm_monitor_scraper.py
"""
import os
import time
from collections import Counter

from bs4 import BeautifulSoup

PM_MONITOR_URL = (
    "http://tncpisapg.tn.chipmos.com.tw/APG/APGPROD/EQUIPMENT/"
    "wFrmPMRepairSetupMonitor/Default.aspx"
    "?isCopy=True&FuncId=57&ServerName=CPIS&UserName=EQS01"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DRIVER_PATH = os.path.join(SCRIPT_DIR, "msedgedriver.exe")

# JS把Syncfusion Grid畫出來要等多久，太短可能表格還沒渲染完、抓到空表格
WAIT_SECONDS = 15

SCREENSHOT_PATH = os.path.join(SCRIPT_DIR, "pm_monitor_debug.png")


class PmMonitorError(RuntimeError):
    pass


def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service

    if not os.path.exists(DRIVER_PATH):
        raise PmMonitorError(
            f"找不到 {DRIVER_PATH}，請下載跟電腦上Edge版本相符的msedgedriver.exe放進da_bot資料夾"
        )
    opts = Options()
    for a in ["--headless=new", "--window-size=1600,1200", "--disable-gpu", "--no-sandbox"]:
        opts.add_argument(a)
    return webdriver.Edge(service=Service(executable_path=DRIVER_PATH), options=opts)


def _find_grid_html(driver, max_depth=5, log=None):
    """
    遞迴切換進每一層frame，找到含ENTITY+STATUS表頭的那一層，回傳其HTML；
    找不到就回傳None(呼叫端自己決定要不要退回用最外層的page_source)。
    log給的話會記錄每一層抓到的HTML長度，抓不到表格時方便印出來對照。
    """
    html = driver.page_source
    if log is not None:
        log.append(len(html))
    if "ENTITY" in html.upper() and "STATUS" in html.upper():
        return html
    if max_depth <= 0:
        return None

    frame_count = len(driver.find_elements("tag name", "frame") + driver.find_elements("tag name", "iframe"))
    for i in range(frame_count):
        frames = driver.find_elements("tag name", "frame") + driver.find_elements("tag name", "iframe")
        try:
            driver.switch_to.frame(frames[i])
        except Exception:
            continue
        result = _find_grid_html(driver, max_depth - 1, log)
        driver.switch_to.parent_frame()
        if result:
            return result
    return None


def _switch_to_frame_with_element(driver, element_id, max_depth=5):
    """
    遞迴切換進每一層frame，找到含指定id元素的那一層就停在那一層(留在那一層
    不切回去，方便呼叫端直接操作該元素)，回傳True；找不到的話切回最外層、
    回傳False。
    """
    try:
        driver.find_element("id", element_id)
        return True
    except Exception:
        pass
    if max_depth <= 0:
        return False

    frame_count = len(driver.find_elements("tag name", "frame") + driver.find_elements("tag name", "iframe"))
    for i in range(frame_count):
        frames = driver.find_elements("tag name", "frame") + driver.find_elements("tag name", "iframe")
        try:
            driver.switch_to.frame(frames[i])
        except Exception:
            continue
        if _switch_to_frame_with_element(driver, element_id, max_depth - 1):
            return True
        driver.switch_to.parent_frame()
    return False


def _select_oper_kind_and_fetch(driver, oper_kind, timeout=15):
    """
    截圖發現：全新開的無頭瀏覽器一開始Oper Kind預設是"FRONT END"、下面的
    機況表格整個是空的，完全沒有資料——實際使用者手動操作時之所以一開頁面
    就有資料，是瀏覽器記得之前選過"D/A"、按過Fetch的緣故(session/ViewState)，
    全新session沒有這個記憶。這裡就是補上這個動作：切進控制項所在的那個
    frame，把Oper Kind下拉選單選成oper_kind、按下Fetch按鈕，觸發JS真正
    去要一次資料。

    第一版用固定time.sleep(3)才開始找控制項所在的frame，實測發現常常
    frame還沒完全載入完(巢狀好幾層、每層都要各自發一次請求)，導致找不到
    ddlOperKind、整個動作默默失敗，畫面還是停在FRONT END/空白。這裡改成
    在timeout秒內每秒重試一次，並回傳status字串描述實際發生的狀況(不再
    默默吞掉例外)，方便印出來診斷到底是哪一步卡住。
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select

    deadline = time.time() + timeout
    found = False
    while time.time() < deadline:
        driver.switch_to.default_content()
        if _switch_to_frame_with_element(driver, "ddlOperKind"):
            found = True
            break
        time.sleep(1)

    if not found:
        driver.switch_to.default_content()
        return f"[選單] 等了{timeout}秒還是找不到ddlOperKind下拉選單所在的frame"

    try:
        Select(driver.find_element(By.ID, "ddlOperKind")).select_by_visible_text(oper_kind)
        driver.find_element(By.ID, "btnFetch").click()
        status = f"[選單] 已選{oper_kind}、按下Fetch"
    except Exception as e:
        status = f"[選單] 找到frame了，但選取/點擊失敗: {type(e).__name__}: {e}"
    finally:
        driver.switch_to.default_content()
    return status


def fetch_pm_monitor_html(wait_seconds=WAIT_SECONDS, oper_kind="D/A"):
    """開無頭瀏覽器，選好Oper Kind、按Fetch觸發JS載入，等機況表格畫出來，
    回傳渲染後含目標表格的那一層HTML。

    找不到表格時，會先存一張目前畫面的截圖(SCREENSHOT_PATH)再丟例外——
    比起純文字的錯誤訊息，截圖能直接看出JS到底畫出了什麼(卡在載入中、
    畫面是空的、還是根本跳到別的頁面)，不用再靠猜的。
    """
    driver = _make_driver()
    try:
        driver.get(PM_MONITOR_URL)
        select_status = _select_oper_kind_and_fetch(driver, oper_kind)
        print(select_status)
        time.sleep(wait_seconds)
        log = []
        html = _find_grid_html(driver, log=log)
        if html is None:
            driver.switch_to.default_content()
            try:
                driver.save_screenshot(SCREENSHOT_PATH)
                shot_note = f"已存截圖到 {SCREENSHOT_PATH}，把這張圖傳給Claude看"
            except Exception as e:
                shot_note = f"存截圖也失敗了: {type(e).__name__}: {e}"
            raise PmMonitorError(
                "找不到含ENTITY+STATUS表頭的表格，可能JS還沒渲染完"
                f"(可以拉長wait_seconds，目前是{wait_seconds}秒)，或頁面結構變了。\n"
                f"{select_status}\n"
                f"掃過的每一層HTML長度: {log}\n{shot_note}"
            )
        return html
    finally:
        driver.quit()


def parse_pm_monitor_html(html):
    """
    解析渲染後的HTML，抓OPER/ENTITY/MODEL/STATUS/LOT NO/Bond ID/WIP/
    IN TIME/OUTPLAN/JCODE/OPERATOR這個表格，回傳list of dict(欄位名照表頭原樣)。

    Syncfusion Grid這類元件很常見的做法是把「表頭」跟「資料內容」拆成兩個
    不同的<table>(方便凍結表頭、內容區單獨捲動)，不是同一個<table>裡表頭
    加資料列這種單純結構。原本邏輯要求表頭跟資料列在同一個<table>裡，
    抓到表頭表格(通常沒有資料列，或資料列數=0)就直接回傳，導致實際上真的
    有資料的表格根本沒被掃到，永遠回傳空清單。

    改成：先找出表頭在哪一列(欄位名稱清單)，再不限定同一個<table>，直接
    在整份HTML裡找「欄位數跟表頭一樣」的所有<tr>當資料列，跳過表頭本身
    (表頭列可能因為凍結表頭機制重複出現)。
    """
    soup = BeautifulSoup(html, "html.parser")
    header = None
    for tr in soup.find_all("tr"):
        candidate = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        candidate_upper = [h.upper() for h in candidate]
        if "ENTITY" in candidate_upper and "STATUS" in candidate_upper:
            header = candidate
            break

    if header is None:
        return []

    records = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) != len(header) or cells == header or not any(cells):
            continue
        records.append(dict(zip(header, cells)))
    return records


def fetch_pm_monitor_records(wait_seconds=WAIT_SECONDS, oper_kind="D/A"):
    html = fetch_pm_monitor_html(wait_seconds, oper_kind)
    return parse_pm_monitor_html(html)


def _dump_entity_context(html, needle="ENTITY", radius=250, max_hits=3):
    """
    診斷用：直接把html裡"ENTITY"字樣附近的原始內容印出來(而不是再猜表格
    結構去改程式碼)，這樣可以直接看到真實的HTML長什麼樣，不用再靠猜的。
    """
    upper = html.upper()
    start = 0
    hits = 0
    print(f"\n[診斷] 在HTML裡搜尋\"{needle}\"字樣附近的原始內容(前{max_hits}筆)：")
    while hits < max_hits:
        idx = upper.find(needle, start)
        if idx == -1:
            break
        s, e = max(0, idx - radius), min(len(html), idx + radius)
        print(f"\n--- 第{hits + 1}筆(位置{idx}) ---")
        print(html[s:e])
        start = idx + len(needle)
        hits += 1
    if hits == 0:
        print(f"(完全沒找到\"{needle}\"字樣，HTML長度={len(html)})")


if __name__ == "__main__":
    html = fetch_pm_monitor_html()
    records = parse_pm_monitor_html(html)
    print(f"共擷取到 {len(records)} 筆")
    print("[STATUS統計]", dict(Counter(r.get("STATUS", "") for r in records)))
    print("\n[前5筆原始資料]")
    for r in records[:5]:
        print(r)
    if not records:
        _dump_entity_context(html)
