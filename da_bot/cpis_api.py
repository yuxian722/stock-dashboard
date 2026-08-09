# -*- coding: utf-8 -*-
"""
CPIS/APG HTTP API 模組(取代 cpis_scraper.py / cpis_utilization_scraper.py 原本的
Selenium 附身模式)

原本這兩支腳本靠附身模式Edge(--remote-debugging-port=9222)操作已登入的分頁，
長期卡在「除錯模式Edge開不起來」的環境問題(單一實例限制、殘留行程沒清乾淨等)。
同事的 APG_Dashboard 專案已證實 CPIS 可以純用 urllib.request 直接發HTTP請求
登入+查詢，完全不需要開瀏覽器，這裡把這套方法整合成模組，比照 teamplus_api.py
的封裝方式：只負責「登入 + 把查詢結果原始HTML抓回來」，實際的表格解析/欄位對應/
資料清洗邏輯仍留在 cpis_scraper.py / cpis_utilization_scraper.py 裡(用
BeautifulSoup，跟原本一致，只是資料來源從 driver.page_source 換成這裡回傳的HTML)。

原理：CPIS是傳統ASP.NET WebForms系統，登入與查詢都要帶上隱藏欄位
__VIEWSTATE / __VIEWSTATEGENERATOR / __EVENTVALIDATION(先GET頁面把值抓出來，
再原樣連同帳密/查詢條件POST回去)。用http.cookiejar讓session cookie自動夾帶在
後續請求中，就等於「登入後」的持續session，跟瀏覽器操作是同一件事。

用法：
    import cpis_api
    html = cpis_api.fetch_ee_maintenance_html("20260716", "20260717", "B*")
    html_list = cpis_api.fetch_utilization_html("20260809", "20260809")

前置：
    da_bot資料夾下要有 config.txt(複製 config.txt.example 改名，填入
    apg_user/apg_password/util_user/util_password)。
"""
import http.cookiejar
import re
import urllib.error
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

import config

DOMAINS = {
    "tncpisapg.tn.chipmos.com.tw": {
        "login": "http://tncpisapg.tn.chipmos.com.tw/CPISWeb/Logon.aspx?ReturnUrl=%2fCPISWeb%2fDefault.aspx",
        "default": "http://tncpisapg.tn.chipmos.com.tw/CPISWeb/Default.aspx",
    },
    "tncpis.tn.chipmos.com.tw": {  # Utilization 用，走 APG 子系統獨立登入
        "login": (
            "http://tncpis.tn.chipmos.com.tw/APG/Logon.aspx?ReturnUrl="
            "%2fAPG%2fAPGPROD%2fEQUIPMENT%2fwFrmUtilizationAnalysis%2futil_overa2.aspx"
        ),
        "default": (
            "http://tncpis.tn.chipmos.com.tw/APG/APGPROD/EQUIPMENT/"
            "wFrmUtilizationAnalysis/Default.aspx?isCopy=True&FuncId=58"
        ),
    },
}

AUTH_FAIL = ("Logon.aspx", "TimeOut.aspx", "系統停滯過久", "請重新登入")

EE_FRAME_URL = (
    "http://tncpisapg.tn.chipmos.com.tw/APG/APGREPORT/EE/"
    "wFrmEEMaintenanceRecord/maintenance_record_h.aspx"
)
EE_REFERER_URL = (
    "http://tncpisapg.tn.chipmos.com.tw/APG/APGREPORT/EE/"
    "wFrmEEMaintenanceRecord/Default.aspx"
)

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


def extract_form_fields(html):
    """
    把HTML裡第一個<form>目前的欄位狀態(name -> value)整理成dict，包含
    __VIEWSTATE等隱藏欄位、文字框目前的值、下拉選單目前選到的選項、
    已勾選的checkbox/radio。用來複製「表單上沒特別去改的欄位維持原樣送出」
    這種真實瀏覽器postback的行為，只要再覆蓋我們真正要改的幾個欄位就好，
    不用每個欄位都自己猜值。
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if not form:
        return {}

    fields = {}
    for tag in form.find_all(["input", "select", "textarea"]):
        name = tag.get("name")
        if not name:
            continue
        tag_name = tag.name
        if tag_name == "input":
            input_type = (tag.get("type") or "text").lower()
            if input_type in ("checkbox", "radio"):
                if tag.has_attr("checked"):
                    fields[name] = tag.get("value", "on")
            elif input_type in ("submit", "button", "image", "reset", "file"):
                continue
            else:
                fields[name] = tag.get("value", "")
        elif tag_name == "select":
            options = tag.find_all("option")
            selected = next((o for o in options if o.has_attr("selected")), None)
            if selected is None and options:
                selected = options[0]
            fields[name] = selected.get("value", selected.get_text(strip=True)) if selected else ""
        elif tag_name == "textarea":
            fields[name] = tag.get_text()
    return fields


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
# 登入
# ---------------------------------------------------------------------------

def do_login(opener, host, user, pwd):
    info = DOMAINS[host]
    html, _ = _read(opener, info["login"])
    payload = {
        "__VIEWSTATE": extract_input(html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": extract_input(html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": extract_input(html, "__EVENTVALIDATION"),
        "UserName": user,
        "Password": pwd,
        "Login.x": "61",
        "Login.y": "5",
    }
    _, final_url = _post_form(opener, info["login"], payload, referer=info["login"])
    return "Logon.aspx" not in final_url


def login():
    """建立opener並登入APG主站(EE Maintenance用)，回傳已登入的opener。"""
    cfg = config.load()
    config.require(cfg, "apg_user", "apg_password")
    opener = build_opener()
    if not do_login(opener, "tncpisapg.tn.chipmos.com.tw", cfg["apg_user"], cfg["apg_password"]):
        raise CpisAuthError("APG登入失敗，請確認config.txt的apg_user/apg_password")
    return opener


# ---------------------------------------------------------------------------
# EE Maintenance Record：查詢表單頁 -> POST查詢條件 -> 回傳結果HTML
# ---------------------------------------------------------------------------

def fetch_ee_maintenance_html(date_start, date_end, entity_pattern="*", opener=None):
    """
    登入APG站並查詢EE Maintenance Record，回傳查詢結果頁的原始HTML(字串)，
    交給cpis_scraper.py用BeautifulSoup解析(跟原本Selenium版的parse_result_table
    邏輯完全一致，只是HTML的來源從driver.page_source換成這裡回傳的字串)。

    date_start/date_end格式跟原本Selenium版一致，YYYYMMDD。
    entity_pattern是機台代號萬用字元查詢(例如"B*")，對應原始表單裡的txtentity欄位。
    """
    opener = opener or login()

    req = urllib.request.Request(EE_FRAME_URL)
    req.add_header("Referer", EE_REFERER_URL)
    html_form, form_url = _read(opener, req)
    if is_auth_fail(html_form, form_url):
        raise CpisAuthError("EE驗證失敗(取表單)")

    # 先複製表單目前所有欄位的值(包含VIEWSTATE等隱藏欄位)，只覆蓋我們真正要改的幾個，
    # 其餘欄位維持頁面預設值，避免亂猜欄位值反而讓查詢條件跟預期不同
    fields = extract_form_fields(html_form)
    fields["txtStart_date"] = date_start
    fields["txtEnd_date"] = date_end
    if "txtentity" in fields:
        fields["txtentity"] = entity_pattern

    # Operation「Select all」：原始頁面用DropDownCheckBoxes1_sll這個checkbox觸發JS
    # 把底下每個站別checkbox(DropDownCheckBoxes1$xxx)全部勾選，這裡直接把偵測到的
    # 每一個都設成"on"，效果相同(勾「Select all」全選才不會讓查詢條件互相打架變成No Data)
    if "DropDownCheckBoxes1_sll" in fields:
        fields["DropDownCheckBoxes1_sll"] = "on"
    for name in list(fields):
        if name.startswith("DropDownCheckBoxes1$"):
            fields[name] = "on"

    fields["btnFetch"] = fields.get("btnFetch") or "Fetch"

    html_result, final_url = _post_form(opener, EE_FRAME_URL, fields, referer=EE_FRAME_URL, timeout=60)
    if is_auth_fail(html_result, final_url):
        raise CpisAuthError("EE POST驗證失敗")

    return html_result


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
