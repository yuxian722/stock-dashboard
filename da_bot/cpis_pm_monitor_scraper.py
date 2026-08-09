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
WAIT_SECONDS = 8


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


def _find_grid_html(driver, max_depth=5):
    """
    遞迴切換進每一層frame，找到含ENTITY+STATUS表頭的那一層，回傳其HTML；
    找不到就回傳None(呼叫端自己決定要不要退回用最外層的page_source)。
    """
    html = driver.page_source
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
        result = _find_grid_html(driver, max_depth - 1)
        driver.switch_to.parent_frame()
        if result:
            return result
    return None


def fetch_pm_monitor_html(wait_seconds=WAIT_SECONDS):
    """開無頭瀏覽器，等JS畫出機況表格，回傳渲染後含目標表格的那一層HTML。"""
    driver = _make_driver()
    try:
        driver.get(PM_MONITOR_URL)
        time.sleep(wait_seconds)
        html = _find_grid_html(driver)
        if html is None:
            raise PmMonitorError(
                "找不到含ENTITY+STATUS表頭的表格，可能JS還沒渲染完"
                f"(可以拉長wait_seconds，目前是{wait_seconds}秒)，或頁面結構變了"
            )
        return html
    finally:
        driver.quit()


def parse_pm_monitor_html(html):
    """
    解析渲染後的HTML，抓OPER/ENTITY/MODEL/STATUS/LOT NO/Bond ID/WIP/
    IN TIME/OUTPLAN/JCODE/OPERATOR這個表格，回傳list of dict(欄位名照表頭原樣)。
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header = [c.get_text(strip=True) for c in rows[0].find_all(["td", "th"])]
        header_upper = [h.upper() for h in header]
        if "ENTITY" not in header_upper or "STATUS" not in header_upper:
            continue

        records = []
        for row in rows[1:]:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) != len(header):
                continue
            records.append(dict(zip(header, cells)))
        return records

    return []


def fetch_pm_monitor_records(wait_seconds=WAIT_SECONDS):
    html = fetch_pm_monitor_html(wait_seconds)
    return parse_pm_monitor_html(html)


if __name__ == "__main__":
    records = fetch_pm_monitor_records()
    print(f"共擷取到 {len(records)} 筆")
    print("[STATUS統計]", dict(Counter(r.get("STATUS", "") for r in records)))
    print("\n[前5筆原始資料]")
    for r in records[:5]:
        print(r)
