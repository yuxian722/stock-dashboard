# -*- coding: utf-8 -*-
"""
CPIS/APG HTTP API 模組(取代 cpis_scraper.py / cpis_utilization_scraper.py 原本的
Selenium 附身模式)

原本這兩支腳本靠附身模式Edge(--remote-debugging-port=9222)操作已登入的分頁，
長期卡在「除錯模式Edge開不起來」的環境問題(單一實例限制、殘留行程沒清乾淨等)。
這裡把純urllib.request直接發HTTP請求登入+查詢的方法整合成模組，比照
teamplus_api.py的封裝方式：只負責「登入 + 把查詢結果抓回來」，實際的資料解析/
欄位對應/清洗邏輯留在 cpis_scraper.py / cpis_utilization_scraper.py 裡。

EE Maintenance走的是report產生端點(maintenance_record_r.aspx)，回傳EJP_*.xls
報表檔案(舊版Excel/BIFF格式)，由cpis_scraper.py用xlrd解析；Utilization走的是
資料頁直接GET，回傳HTML，由cpis_utilization_scraper.py用BeautifulSoup解析。

原理：CPIS是傳統ASP.NET WebForms系統，登入與查詢都要帶上隱藏欄位
__VIEWSTATE / __VIEWSTATEGENERATOR / __EVENTVALIDATION(先GET頁面把值抓出來，
再原樣連同帳密/查詢條件POST回去)。用http.cookiejar讓session cookie自動夾帶在
後續請求中，就等於「登入後」的持續session，跟瀏覽器操作是同一件事。

用法：
    import cpis_api
    xls_chunks = cpis_api.fetch_ee_maintenance_xls("20260716", "20260717", "BA*")
    html_list = cpis_api.fetch_utilization_html("20260809", "20260809")

前置：
    da_bot資料夾下要有 config.txt(複製 config.txt.example 改名，填入
    apg_user/apg_password/util_user/util_password)。
"""
import datetime
import http.cookiejar
import re
import urllib.error
import urllib.parse
import urllib.request

import config

AUTH_FAIL = ("Logon.aspx", "TimeOut.aspx", "系統停滯過久", "請重新登入")

EE_R_BASE = "http://tncpisapg.tn.chipmos.com.tw"
EE_R_PATH = "/APG/APGREPORT/EE/wFrmEEMaintenanceRecord/maintenance_record_r.aspx"

# 2026/08/12使用者實測發現：EE_R_PATH(maintenance_record_r.aspx)這個「report
# 產生端點」的shift查詢參數(&shift=AD)其實沒有被伺服器端真正套用(shift=AD
# 查出來的筆數跟shift=None完全一樣)。真正有實作Shift篩選的是另一個查詢
# 表單頁面maintenance_record_h.aspx，純urllib模擬POST這個表單試過兩輪
# (補__EVENTTARGET/__EVENTARGUMENT、先訪問FuncId入口頁「暖身」)都還是回
# 500 Internal Server Error，改用cpis_ee_shift_scraper.py的無頭瀏覽器
# 做法，不在這個模組裡處理。

UTIL_BASE = "http://tncpis.tn.chipmos.com.tw"
UTIL_DATA_PATH = "/APG/APGPROD/EQUIPMENT/wFrmUtilizationAnalysis/util_overa2.aspx"

# session過期時查詢途中可能出現的重新登入表單標記(entquery這類頁面逾時會有btnReLogon)
RELOGON_MARKER = "btnReLogon"

MAX_QUERY_ATTEMPTS = 2  # 查詢途中若偵測到session過期，最多重試幾次(跟原本Selenium版一致)
MAX_FRAME_DEPTH = 5  # 遞迴掃描frame的最大深度(跟原本Selenium版FRAME_SCAN_MAX_DEPTH一致)

_ENCODINGS = ("utf-8", "big5", "cp950")


class CpisAuthError(RuntimeError):
    """登入失敗，或session過期重新登入後仍然失敗。"""


# ---------------------------------------------------------------------------
# 建立帶 cookiejar 的 opener
# ---------------------------------------------------------------------------

def build_opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    # entquery.aspx這類頁面會擋過於陽春的請求，必須用完整的瀏覽器標頭，否則會被導向逾時頁
    op.addheaders = [
        (
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        ),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        ("Accept-Language", "zh-TW,zh;q=0.9,en;q=0.8"),
        ("Cache-Control", "no-cache"),
        ("Upgrade-Insecure-Requests", "1"),
    ]
    return op


# ---------------------------------------------------------------------------
# 從HTML抓ASP.NET隱藏欄位值 / 目前表單狀態 / 編碼自動偵測 / 驗證失敗判斷
# ---------------------------------------------------------------------------

def extract_input(html, name):
    m = re.search(r'<input[^>]*\bname=["\']?' + re.escape(name) + r'["\']?[^>]*>', html, re.I)
    if not m:
        return ""
    vm = re.search(r'value=["\']([^"\']*)["\']', m.group(0), re.I)
    return vm.group(1) if vm else ""


def decode_best(raw):
    """頁面混用utf-8/big5/cp950，挑亂碼字元數最少的編碼。"""
    best, best_bad, best_enc = "", float("inf"), _ENCODINGS[0]
    for enc in _ENCODINGS:
        try:
            s = raw.decode(enc, errors="replace")
        except LookupError:
            continue
        bad = s.count("�")
        if bad < best_bad:
            best, best_bad, best_enc = s, bad, enc
    return best, best_enc


def is_auth_fail(html, url):
    return any(t in url or t in html[:3000] for t in AUTH_FAIL)


def _read(opener, req_or_url, timeout=30):
    with opener.open(req_or_url, timeout=timeout) as r:
        html, _ = decode_best(r.read())
        return html, r.geturl()


def _post_form(opener, url, fields, referer=None, timeout=30):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", referer or url)
    return _read(opener, req, timeout=timeout)


# ---------------------------------------------------------------------------
# EE Maintenance Record
#
# 這裡改用「report產生端點」maintenance_record_r.aspx：查詢條件直接是query
# string，登入時把ReturnUrl指到這個帶好條件的網址，登入成功會直接被redirect到
# 這裡，伺服器端算好報表後回傳一頁「Report Generate Successful」+ EJP_*.xls
# 報表連結，下載該檔案(舊版Excel/BIFF格式，用xlrd解析)就是完整資料。
# 不需要像maintenance_record_h.aspx那個查詢表單一樣填Operation複選框，
# 也不會被表單前端驗證(entity/JobCode長度規則)卡住——這個端點的query string
# 沒有那些檢查。
# (此流程比照使用者自己另一個已實測驗證過的dashboard(server.py)的作法照搬)
# ---------------------------------------------------------------------------

_EJP_URL_RE = re.compile(r'https?://\S+/APG/assyfab/cpis/report/EJP_\d+\.xls')
_EJP_ID_RE = re.compile(r'EJP_(\d+)\.xls')


def _check_entity_pattern(entity_pattern):
    """
    entity欄位建議：不可為空，且扣掉萬用字元(*/?)後至少要有2個字元(例如"BA*"
    而不是"B*")，這是原本maintenance_record_h.aspx表單驗證觀察到的規則，這裡
    先擋一手避免明顯會查不到東西的輸入。
    """
    literal_len = len((entity_pattern or "").replace("*", "").replace("?", ""))
    if literal_len < 2:
        raise ValueError(
            f"entity_pattern={entity_pattern!r} 不建議使用：扣掉萬用字元(*/?)後"
            "至少要有2個字元(例如用'BA*'而不是'B*')"
        )


def _ee_query_string(date_start, date_end, entity, jobcode="", shift="None"):
    return (
        "HIDCOUNT=1&pkg_type=T"
        f"&start_date={date_start}&end_date={date_end}"
        f"&entity={entity}&shift={shift}&floor=A2&operation=None"
        f"&etag=None&jobcode={jobcode}&enginerr=&value=WD&oper_type=0"
        "&dept=None&description=&bd_id=None&assylot=&product="
    )


def _find_ejp_url(html):
    """從HTML找EJP_*.xls報表的完整下載URL，抓不到回傳None。"""
    m = _EJP_URL_RE.search(html)
    if m:
        return m.group(0)
    m = _EJP_ID_RE.search(html)
    if m:
        return f"{EE_R_BASE}/APG/assyfab/cpis/report/EJP_{m.group(1)}.xls"
    return None


def _fetch_ee_maintenance_chunk(date_start, date_end, entity, jobcode="", shift="None"):
    """單一區間(<=30天)查詢，回傳EJP報表(.xls)的原始bytes。

    shift：CPIS查詢頁「Shift」下拉選單的值(None/AD/AN/BD/BN，2026/08/10
    使用者截圖確認，A/B是班組、D/N是早/夜班)。這是「查詢時的過濾參數」，
    不是報表欄位——同一個時段查shift=None會拿到AD+AN+BD+BN全部班別的
    紀錄合在一起，沒辦法事後從資料裡分辨出處，要分班別就得帶不同的shift
    值分開查。
    """
    cfg = config.load()
    config.require(cfg, "apg_user", "apg_password")

    qs = _ee_query_string(date_start, date_end, entity, jobcode, shift)
    ee_path_qs = f"{EE_R_PATH}?{qs}"
    login_url = f"{EE_R_BASE}/APG/Logon.aspx?ReturnUrl=" + urllib.parse.quote(ee_path_qs, safe="")

    opener = build_opener()
    login_html, _ = _read(opener, login_url, timeout=20)
    payload = {
        "__VIEWSTATE": extract_input(login_html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": extract_input(login_html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": extract_input(login_html, "__EVENTVALIDATION"),
        "UserName": cfg["apg_user"],
        "Password": cfg["apg_password"],
        "Login.x": "50",
        "Login.y": "15",
    }
    html, final_url = _post_form(opener, login_url, payload, referer=login_url, timeout=120)
    if "logon" in final_url.lower():
        raise CpisAuthError(f"EE Maintenance登入失敗，仍停留在登入頁：{final_url}")

    xls_url = _find_ejp_url(html)
    if not xls_url:
        preview = re.sub(r"\s+", " ", html)[:200]
        raise CpisAuthError(f"EE Maintenance查無EJP報表連結(可能這段區間沒有資料)：{preview!r}")

    with opener.open(xls_url, timeout=90) as r:
        raw = r.read()
    if len(raw) < 1000:
        raise CpisAuthError(f"EE Maintenance報表下載失敗或內容過短({len(raw)} bytes)")
    return raw


def fetch_ee_maintenance_xls(date_start, date_end, entity="BA*", jobcode="", shift="None"):
    """
    查詢EE Maintenance Record，回傳EJP報表(.xls, 舊版BIFF格式)原始bytes的清單
    (區間>30天時會自動拆成多段查詢，所以是清單而非單一結果)。

    date_start/date_end格式YYYYMMDD。依CPIS查詢頁規則(來自實測驗證過的既有
    dashboard)：
      - 區間<=7天：entity可用萬用字元，jobcode可留空
      - 區間8天~1個月：entity與jobcode皆為必填(可用萬用字元，預設用*涵蓋所有JobCode)
      - 區間>1個月：自動切成多段(每段<=30天)分別查詢

    shift：見_fetch_ee_maintenance_chunk()說明，預設"None"(不篩班別，維持
    原本行為)，2026/08/10使用者要求新增班別(AD/AN/BD/BN)查詢時才會帶別的值。
    """
    _check_entity_pattern(entity)

    d1 = datetime.datetime.strptime(date_start, "%Y%m%d")
    d2 = datetime.datetime.strptime(date_end, "%Y%m%d")
    total_days = (d2 - d1).days + 1

    if total_days > 30:
        chunks = []
        cur = d1
        while cur <= d2:
            chunk_end = min(cur + datetime.timedelta(days=29), d2)
            chunks.extend(
                fetch_ee_maintenance_xls(
                    cur.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"), entity, jobcode, shift
                )
            )
            cur = chunk_end + datetime.timedelta(days=1)
        return chunks

    jc = jobcode
    if total_days > 7 and not jc:
        jc = "*"

    return [_fetch_ee_maintenance_chunk(date_start, date_end, entity, jc, shift)]


# ---------------------------------------------------------------------------
# Utilization Analysis：APG子系統獨立登入 -> 直接GET資料頁 -> 回傳結果HTML列表
# ---------------------------------------------------------------------------

def _utilization_data_url(date_start, date_end, util_oper):
    return (
        UTIL_BASE + UTIL_DATA_PATH +
        "?sort_by=&ismfgmc=&HidCount=&paging=False&shift=None"
        f"&start_date={date_start}&end_date={date_end}"
        f"&operation={urllib.parse.quote(util_oper)}"
        "&types=p&isinlinecombine=&bd_id=None&getqty=N&sort=None&pline="
    )


def _iframe_srcs(html):
    return [m.group(1) for m in re.finditer(r'<i?frame[^>]*\bsrc=["\']([^"\']+)["\']', html, re.I)]


def _login_utilization(date_start, date_end, util_oper):
    cfg = config.load()
    config.require(cfg, "util_user", "util_password")

    data_url = _utilization_data_url(date_start, date_end, util_oper)
    login_url = UTIL_BASE + "/APG/Logon.aspx?ReturnUrl=" + urllib.parse.quote(
        data_url.replace(UTIL_BASE, ""), safe=""
    )

    opener = build_opener()
    login_html, _ = _read(opener, login_url, timeout=20)
    payload = {
        "__VIEWSTATE": extract_input(login_html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": extract_input(login_html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": extract_input(login_html, "__EVENTVALIDATION"),
        "UserName": cfg["util_user"],
        "Password": cfg["util_password"],
        "Login.x": "61",
        "Login.y": "5",
    }
    html, final_url = _post_form(opener, login_url, payload, referer=login_url, timeout=60)
    if is_auth_fail(html, final_url):
        raise CpisAuthError("Utilization驗證失敗")
    return opener, html, final_url


def _collect_html_recursive(opener, html, url, depth):
    """
    遞迴掃描frame/iframe，把每一層的HTML都收集起來(對應原本Selenium版
    collect_all_tables_recursive遞迴掃描所有frame的邏輯)。直接GET資料頁通常
    已經是自包含的完整頁面，不會再有巢狀frame，這裡是保險，真的遇到才會用到。
    """
    htmls = [html]
    if depth <= 0:
        return htmls

    for src in _iframe_srcs(html):
        frame_url = urllib.parse.urljoin(url, src)
        try:
            frame_html, frame_final_url = _read(opener, frame_url, timeout=30)
        except (urllib.error.URLError, OSError):
            continue
        if is_auth_fail(frame_html, frame_final_url):
            continue
        htmls.extend(_collect_html_recursive(opener, frame_html, frame_final_url, depth - 1))

    return htmls


def fetch_utilization_html(date_start, date_end, util_oper="DA", max_attempts=MAX_QUERY_ATTEMPTS):
    """
    走APG子系統獨立登入查詢Utilization Analysis，登入成功會直接redirect到資料頁。
    回傳HTML字串清單(通常只有一個元素)，交給cpis_utilization_scraper.py用
    BeautifulSoup解析每個頁面的<table>再合併(跟原本Selenium版的parse_tables邏輯
    完全一致)。查詢途中若偵測到session過期，最多重試max_attempts次(重新登入)。
    """
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            opener, html, final_url = _login_utilization(date_start, date_end, util_oper)
            if RELOGON_MARKER in html:
                # entquery這類頁面逾時會出現重新登入表單，直接視為本次嘗試失敗，重登再試
                raise CpisAuthError("查詢途中session過期(偵測到重新登入表單)")
            return _collect_html_recursive(opener, html, final_url, MAX_FRAME_DEPTH)
        except CpisAuthError as e:
            last_err = e
    raise last_err
