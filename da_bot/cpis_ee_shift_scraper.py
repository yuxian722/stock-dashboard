"""
CPIS EE Maintenance Record 班別(AD/AN/BD/BN)篩選查詢(Selenium無頭瀏覽器版，
2026/08/12使用者要求)

背景：cpis_api.py純urllib版本(直接模擬POST maintenance_record_h.aspx，
帶__VIEWSTATE/__EVENTVALIDATION/85項Operation複選框欄位)實測連續兩輪都
回500 Internal Server Error——先後補齊__EVENTTARGET/__EVENTARGUMENT、
再改成先訪問帶FuncId的選單入口頁「暖身」都沒解決。這個表單牽涉的ASP.NET
postback細節(DropDownCheckBoxes控制項的用戶端狀態、或其他沒能從截圖
完整還原的隱藏欄位)光靠純HTTP模擬太容易漏掉、難以debug。

改用cpis_pm_monitor_scraper.py已經驗證可行的無頭瀏覽器做法：真的打開一個
無頭Edge，把頁面渲染出來、實際選好Shift/Entity/日期/E-tag、按下Fetch
按鈕，讓瀏覽器自己處理所有ASP.NET postback細節，跟真人操作完全一樣，
不用自己猜表單欄位——這支腳本的URL/FuncId/流程都是照這個既有可行的
做法照搬(PM Monitor用FuncId=57，這裡是使用者截圖確認的FuncId=569)。

用法：
    import cpis_ee_shift_scraper
    import cpis_scraper
    html = cpis_ee_shift_scraper.fetch_ee_maintenance_shift_html(
        "20260811", "20260811", entity="BA*", shift="AD", etag="S"
    )
    records = cpis_scraper.parse_ee_maintenance_shift_html(html)

前置：跟cpis_pm_monitor_scraper.py一樣，da_bot資料夾下要有msedgedriver.exe
(版本要跟電腦上的Edge相符)。

單獨測試(不用寫程式，直接看結果)：
    python cpis_ee_shift_scraper.py 20260811 20260811 AD
"""
import os
import time

EE_H_URL = (
    "http://tncpisapg.tn.chipmos.com.tw/APG/APGREPORT/EE/wFrmEEMaintenanceRecord/"
    "Default.aspx?isCopy=True&FuncId=569&ServerName=CPIS&UserName=EQS01"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DRIVER_PATH = os.path.join(SCRIPT_DIR, "msedgedriver.exe")

# 2026/08/13使用者實測發現：entity=BA*這種查全樓層的查詢，結果動輒好幾十列
# (使用者截圖看到的表格還要往下捲)，全頁postback要完整渲染完這麼多列可能
# 不只10秒——之前用10秒時，程式沒有丟錯誤(有抓到含表頭的表格，代表頁面
# 骨架已經到了)，但抓到的實際上是列表還沒填滿的中間狀態，篩到特定群組後
# 變成0筆，跟使用者手動測試同樣條件明明有資料對不起來。拉長到20秒，give
# 伺服器更多時間把整頁資料跑完。
WAIT_SECONDS = 20

SCREENSHOT_PATH = os.path.join(SCRIPT_DIR, "ee_shift_debug.png")


class EeShiftScraperError(RuntimeError):
    pass


def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service

    if not os.path.exists(DRIVER_PATH):
        raise EeShiftScraperError(
            f"找不到 {DRIVER_PATH}，請下載跟電腦上Edge版本相符的msedgedriver.exe放進da_bot資料夾"
        )
    opts = Options()
    for a in ["--headless=new", "--window-size=1600,1200", "--disable-gpu", "--no-sandbox"]:
        opts.add_argument(a)
    return webdriver.Edge(service=Service(executable_path=DRIVER_PATH), options=opts)


def _switch_to_frame_with_element(driver, element_id, max_depth=5):
    """跟cpis_pm_monitor_scraper.py同款：遞迴切換進每一層frame，找到含指定
    id元素的那一層就停在那一層(留在那一層，方便呼叫端直接操作該元素)，
    回傳True；找不到就切回最外層、回傳False。"""
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


def _find_result_html(driver, max_depth=5, log=None):
    """跟cpis_pm_monitor_scraper.py的_find_grid_html()同款，改成找含
    MACHINE ID+JOB.CODE表頭(使用者截圖確認的查詢結果表格表頭)的那一層。"""
    html = driver.page_source
    if log is not None:
        log.append(len(html))
    upper = html.upper()
    if "MACHINE ID" in upper and "JOB.CODE" in upper:
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
        result = _find_result_html(driver, max_depth - 1, log)
        driver.switch_to.parent_frame()
        if result:
            return result
    return None


def _fill_form_and_fetch(driver, date_start, date_end, entity, shift, etag, timeout=15):
    """
    切進查詢表單所在的frame，填好Date Range/Entity/Shift/E-tag、按下Fetch。
    跟cpis_pm_monitor_scraper.py的_select_oper_kind_and_fetch()同款寫法：
    frame巢狀好幾層時，剛導覽過去frame可能還沒完全載入完，在timeout秒內
    每秒重試一次找ddl_shift所在的frame，回傳status字串描述實際發生的狀況
    (不默默吞掉例外，方便診斷)。

    2026/08/12~08/13使用者實測發現三件事：
    1. ddl_etag這個下拉選單的<option value="...">跟畫面顯示的文字不一樣
       (select_by_value("S")找不到對應選項、直接丟例外，導致這支函式
       提早中斷、從沒真的按到Fetch)。改用select_by_visible_text(etag)
       改選畫面上顯示的文字"S"，不是底層value。
    2. E-tag/Shift/Entity/日期都填對、Fetch也真的按下去了，還是查出
       "No Data"。使用者實際手動測試後確認：Operation欄位(一個帶核取
       方塊的下拉控制項，不是普通<select>)雖然DevTools截圖看起來裡面
       每一項預設都打勾，但那個勾勾狀態沒有真的「生效/送出」，一定要
       使用者自己點過一次「Select all」才會真的套用全選——這裡改成
       用Selenium主動點開這個控制項、點擊清單裡第一項(兩次截圖都確認
       "Select all"排在第一個)，模擬使用者這個動作。
    3. Operation控制項的DOM id前綴是"DropDownCheckBoxes1"(使用者截圖
       確認)，跟表單postback欄位名稱DropDownCheckBoxes1$0~$84是同一組
       (eWorld.UI.DropDownCheckBoxes這套第三方ASP.NET控制項的典型
       命名慣例)：外層容器id="DropDownCheckBoxes1_sl"(點它會展開/收合
       清單面板)，面板id="DropDownCheckBoxes1_dv"(裡面才是真正的
       <input type="checkbox">清單)。
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select

    deadline = time.time() + timeout
    found = False
    while time.time() < deadline:
        driver.switch_to.default_content()
        if _switch_to_frame_with_element(driver, "ddl_shift"):
            found = True
            break
        time.sleep(1)

    if not found:
        driver.switch_to.default_content()
        return f"[表單] 等了{timeout}秒還是找不到ddl_shift下拉選單所在的frame"

    try:
        start_input = driver.find_element(By.ID, "txtStart_date")
        start_input.clear()
        start_input.send_keys(date_start)

        end_input = driver.find_element(By.ID, "txtEnd_date")
        end_input.clear()
        end_input.send_keys(date_end)

        entity_input = driver.find_element(By.ID, "txtentity")
        entity_input.clear()
        entity_input.send_keys(entity)

        Select(driver.find_element(By.ID, "ddl_shift")).select_by_value(shift)
        Select(driver.find_element(By.ID, "ddl_etag")).select_by_visible_text(etag)

        # Operation欄位：點開DropDownCheckBoxes控制項、點擊清單第一項("Select
        # all")、再點一次容器把面板收起來(避免蓋住其他欄位/按鈕，導致
        # 後面btnFetch點不到)。找不到這個控制項或點擊失敗不當作致命錯誤
        # (捕捉獨立的例外，不中斷整個流程)——就算Operation真的沒選成功，
        # 還是讓Fetch照樣按下去，靠最終結果(有沒有資料)反映問題，而不是
        # 卡在這裡讓其他明明成功的欄位也白填。
        try:
            driver.find_element(By.ID, "DropDownCheckBoxes1_sl").click()
            checkboxes = driver.find_elements(
                By.CSS_SELECTOR, "#DropDownCheckBoxes1_dv input[type=checkbox]"
            )
            if checkboxes:
                checkboxes[0].click()
            driver.find_element(By.ID, "DropDownCheckBoxes1_sl").click()
        except Exception:
            pass

        driver.find_element(By.ID, "btnFetch").click()
        status = f"[表單] 已填好日期({date_start}~{date_end})/entity={entity}/shift={shift}/etag={etag}，按下Fetch"
    except Exception as e:
        status = f"[表單] 找到frame了，但填寫/點擊失敗: {type(e).__name__}: {e}"
    finally:
        driver.switch_to.default_content()
    return status


def fetch_ee_maintenance_shift_html(date_start, date_end, entity="BA*", shift="AD", etag="S",
                                     wait_seconds=WAIT_SECONDS):
    """
    開無頭瀏覽器，實際操作maintenance_record_h.aspx這個查詢表單(選好
    Shift/Entity/日期/E-tag、按Fetch，見_fill_form_and_fetch()說明)，
    回傳結果頁面的HTML，交給cpis_scraper.parse_ee_maintenance_shift_html()
    解析。

    找不到結果表格時，會先存一張目前畫面的截圖(SCREENSHOT_PATH)再丟例外
    ——比起純文字的錯誤訊息，截圖能直接看出卡在哪一步，不用再靠猜的。
    """
    driver = _make_driver()
    try:
        driver.get(EE_H_URL)
        fill_status = _fill_form_and_fetch(driver, date_start, date_end, entity, shift, etag)
        print(fill_status)
        time.sleep(wait_seconds)
        log = []
        html = _find_result_html(driver, log=log)
        if html is None:
            driver.switch_to.default_content()
            try:
                driver.save_screenshot(SCREENSHOT_PATH)
                shot_note = f"已存截圖到 {SCREENSHOT_PATH}，把這張圖傳給Claude看"
            except Exception as e:
                shot_note = f"存截圖也失敗了: {type(e).__name__}: {e}"
            raise EeShiftScraperError(
                "找不到含MACHINE ID+JOB.CODE表頭的結果表格，可能查詢還沒跑完"
                f"(可以拉長wait_seconds，目前是{wait_seconds}秒)，或頁面結構變了。\n"
                f"{fill_status}\n"
                f"掃過的每一層HTML長度: {log}\n{shot_note}"
            )
        return html
    finally:
        driver.quit()


if __name__ == "__main__":
    import sys

    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    import cpis_scraper

    date_start = sys.argv[1] if len(sys.argv) > 1 else "20260811"
    date_end = sys.argv[2] if len(sys.argv) > 2 else date_start
    shift = sys.argv[3] if len(sys.argv) > 3 else "AD"

    html = fetch_ee_maintenance_shift_html(date_start, date_end, shift=shift)
    records = cpis_scraper.parse_ee_maintenance_shift_html(html)
    print(f"共擷取到 {len(records)} 筆")
    for r in records[:10]:
        print(r)
