"""CPIS/APG 站的純 requests(urllib) 封裝，取代 Selenium 附身模式。

CPIS 是 ASP.NET WebForms 系統：先 GET 頁面把 __VIEWSTATE / __VIEWSTATEGENERATOR /
__EVENTVALIDATION 抓出來，再原樣連同帳密／查詢條件 POST 回去；用 http.cookiejar
讓 session cookie 自動夾帶在後續請求中，等同「登入後」的持續 session。

對外主要函式：
    login()                                          -> 登入 APG（EE Maintenance 用），回傳 opener
    fetch_ee_maintenance(start_date, end_date, ...)   -> rows
    fetch_utilization(start_date, end_date, ...)      -> rows
"""

from __future__ import annotations

import http.cookiejar
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

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

# EE Maintenance / Utilization 回傳頁面可能用到的表格 id（新舊版型或走 frame 時 id 前綴會不同）
EE_TABLE_IDS = ("ContentPlaceHolder1_gvData", "gvData")
UTIL_TABLE_IDS = ("ctl00_ContentPlaceHolder1_gvData", "ContentPlaceHolder1_gvData", "gvData")

_ENCODINGS = ("utf-8", "big5", "cp950")


class CpisAuthError(RuntimeError):
    """登入失敗或 session 已過期，重新登入也救不回來。"""


# ---------------------------------------------------------------------------
# 1. 建立帶 cookiejar 的 opener
# ---------------------------------------------------------------------------

def build_opener() -> urllib.request.OpenerDirector:
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    # entquery.aspx 這類頁面會擋過於陽春的請求，必須用完整的瀏覽器標頭，否則會被導向逾時頁
    op.addheaders = [
        (
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        ),
        (
            "Accept",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        ),
        ("Accept-Language", "zh-TW,zh;q=0.9,en;q=0.8"),
        ("Cache-Control", "no-cache"),
        ("Upgrade-Insecure-Requests", "1"),
    ]
    return op


# ---------------------------------------------------------------------------
# 2. 從 HTML 抓 ASP.NET 隱藏欄位值 / 判斷驗證失敗 / 編碼自動偵測
# ---------------------------------------------------------------------------

def extract_input(html: str, name: str) -> str:
    m = re.search(r'<input[^>]*\bname=["\']?' + re.escape(name) + r'["\']?[^>]*>', html, re.I)
    if not m:
        return ""
    vm = re.search(r'value=["\']([^"\']*)["\']', m.group(0), re.I)
    return vm.group(1) if vm else ""


def is_auth_fail(html: str, url: str) -> bool:
    return any(t in url or t in html[:3000] for t in AUTH_FAIL)


def decode_best(raw: bytes) -> tuple[str, str]:
    """頁面混用 utf-8/big5/cp950，挑亂碼字元數最少的編碼。"""
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


def _read(opener: urllib.request.OpenerDirector, req_or_url, timeout: int = 30) -> tuple[str, str]:
    with opener.open(req_or_url, timeout=timeout) as r:
        html, _ = decode_best(r.read())
        return html, r.geturl()


# ---------------------------------------------------------------------------
# 3. 登入
# ---------------------------------------------------------------------------

def do_login(opener: urllib.request.OpenerDirector, host: str, user: str, pwd: str) -> bool:
    info = DOMAINS[host]
    html, _ = _read(opener, info["login"])

    payload = urllib.parse.urlencode(
        {
            "__VIEWSTATE": extract_input(html, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": extract_input(html, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": extract_input(html, "__EVENTVALIDATION"),
            "UserName": user,
            "Password": pwd,
            "Login.x": "61",
            "Login.y": "5",
        }
    ).encode("utf-8")

    req = urllib.request.Request(info["login"], data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", info["login"])
    with opener.open(req, timeout=30) as r:
        return "Logon.aspx" not in r.geturl()


def login() -> urllib.request.OpenerDirector:
    """建立 opener 並登入 APG（EE Maintenance 用），回傳已登入的 opener。"""
    cfg = config.load()
    config.require(cfg, "apg_user", "apg_password")
    opener = build_opener()
    if not do_login(opener, "tncpisapg.tn.chipmos.com.tw", cfg["apg_user"], cfg["apg_password"]):
        raise CpisAuthError("APG 登入失敗，請確認 config.txt 的 apg_user/apg_password")
    return opener


# ---------------------------------------------------------------------------
# 4. 解析回傳 HTML 裡的表格
# ---------------------------------------------------------------------------

class TableParser(HTMLParser):
    """按 id 抓指定表格，逐 row/td 取值（不處理 rowspan，欄位數可能因合併儲存格而不齊）。"""

    def __init__(self, target_id: str):
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self.in_table = self.in_row = self.in_cell = False
        self.depth = 0
        self.cur_row: list[str] = []
        self.cur_cell: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "table":
            if not self.in_table and d.get("id", "") == self.target_id:
                self.in_table, self.depth = True, 1
            elif self.in_table:
                self.depth += 1
        elif self.in_table:
            if tag == "tr" and self.depth == 1:
                self.in_row, self.cur_row = True, []
            elif tag in ("td", "th") and self.in_row:
                self.in_cell, self.cur_cell = True, []
            elif tag == "br" and self.in_cell:
                self.cur_cell.append(" ")

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag == "table":
            self.depth -= 1
            if self.depth == 0:
                self.in_table = False
        elif tag == "tr" and self.depth == 1 and self.in_row:
            self.in_row = False
            if self.cur_row:
                self.rows.append(self.cur_row)
        elif tag in ("td", "th") and self.in_cell:
            self.cur_row.append(re.sub(r"\s+", " ", "".join(self.cur_cell)).strip())
            self.in_cell = False

    def handle_data(self, data):
        if self.in_cell:
            self.cur_cell.append(data)


def parse_table(html: str, table_id: str) -> list[list[str]]:
    p = TableParser(table_id)
    try:
        p.feed(html)
    except Exception:
        pass
    return p.rows


class GridTableParser(HTMLParser):
    """跟 TableParser 一樣，但會把 rowspan 合併的儲存格展開回每一列（rowspan 補值），
    展開後每列的欄位數才會對齊。"""

    def __init__(self, target_id: str):
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self.in_table = self.in_row = self.in_cell = False
        self.depth = 0
        self.pending: dict[int, list] = {}  # col_index -> [value, remaining_rows]
        self.cur_row: list[str] = []
        self.cur_col = 0
        self.cur_cell: list[str] = []
        self.cur_cell_span = 1
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "table":
            if not self.in_table and d.get("id", "") == self.target_id:
                self.in_table, self.depth = True, 1
            elif self.in_table:
                self.depth += 1
        elif self.in_table:
            if tag == "tr" and self.depth == 1:
                self.in_row, self.cur_row, self.cur_col = True, [], 0
            elif tag in ("td", "th") and self.in_row:
                self._fill_pending()
                self.in_cell, self.cur_cell = True, []
                try:
                    self.cur_cell_span = max(1, int(d.get("rowspan", "1")))
                except ValueError:
                    self.cur_cell_span = 1
            elif tag == "br" and self.in_cell:
                self.cur_cell.append(" ")

    def _fill_pending(self):
        while self.cur_col in self.pending:
            value, remaining = self.pending[self.cur_col]
            self.cur_row.append(value)
            remaining -= 1
            if remaining <= 0:
                del self.pending[self.cur_col]
            else:
                self.pending[self.cur_col] = [value, remaining]
            self.cur_col += 1

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag == "table":
            self.depth -= 1
            if self.depth == 0:
                self.in_table = False
        elif tag == "tr" and self.depth == 1 and self.in_row:
            self._fill_pending()
            self.in_row = False
            if self.cur_row:
                self.rows.append(self.cur_row)
        elif tag in ("td", "th") and self.in_cell:
            value = re.sub(r"\s+", " ", "".join(self.cur_cell)).strip()
            self.cur_row.append(value)
            if self.cur_cell_span > 1:
                self.pending[self.cur_col] = [value, self.cur_cell_span - 1]
            self.cur_col += 1
            self.in_cell = False

    def handle_data(self, data):
        if self.in_cell:
            self.cur_cell.append(data)


def parse_table_filled(html: str, table_id: str) -> list[list[str]]:
    p = GridTableParser(table_id)
    try:
        p.feed(html)
    except Exception:
        pass
    return p.rows


def _first_matching_table(html: str, table_ids, filled: bool = False) -> list[list[str]]:
    parse = parse_table_filled if filled else parse_table
    for table_id in table_ids:
        rows = parse(html, table_id)
        if rows:
            return rows
    return []


def _iframe_srcs(html: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r'<iframe[^>]*\bsrc=["\']([^"\']+)["\']', html, re.I)]


# ---------------------------------------------------------------------------
# 5. Session 過期自動續命（entquery 這類頁面逾時會出現 btnReLogon 表單）
# ---------------------------------------------------------------------------

def _has_relogon_form(html: str) -> bool:
    return "btnReLogon" in html


def _post_relogon(opener, url, html):
    data = urllib.parse.urlencode(
        {
            "__VIEWSTATE": extract_input(html, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": extract_input(html, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": extract_input(html, "__EVENTVALIDATION"),
            "btnReLogon": extract_input(html, "btnReLogon") or "重新登入",
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", url)
    return _read(opener, req, timeout=40)


def _open_with_relogon(opener, req_or_url, timeout: int = 30, max_retry: int = 2) -> tuple[str, str]:
    html, url = _read(opener, req_or_url, timeout=timeout)
    retry = 0
    while _has_relogon_form(html) and retry < max_retry:
        html, url = _post_relogon(opener, url, html)
        retry += 1
    return html, url


# ---------------------------------------------------------------------------
# 6. EE Maintenance Record 查詢
# ---------------------------------------------------------------------------

def _ee_oper_fields(html: str, entity: str) -> dict:
    """DropDownCheckBoxes1 的站別代碼是動態產生的，不要寫死 index，改成從表單 HTML
    解析每個 checkbox 對應的站別文字，找出符合 entity 的 checkbox 就標成勾選。
    避免站別代碼改版後對照失準。"""
    fields = {}
    pattern = re.compile(
        r'<input[^>]*type=["\']checkbox["\'][^>]*name=["\'](DropDownCheckBoxes1\$[^"\']+)["\'][^>]*>'
        r'([^<]{0,40})',
        re.I,
    )
    for m in pattern.finditer(html):
        field_name, label_text = m.group(1), m.group(2).strip()
        if entity in label_text:
            fields[field_name] = "on"
    return fields


def fetch_ee_maintenance(
    start_date: str,
    end_date: str,
    ee_entity: str = "DA",
    ee_etag: str = "None(P,R,S,QC)",
    opener: urllib.request.OpenerDirector | None = None,
) -> list[list[str]]:
    """登入 APG 站並查詢 EE Maintenance Record，回傳 parse_table 出來的 rows。"""
    opener = opener or login()

    req = urllib.request.Request(EE_FRAME_URL)
    req.add_header("Referer", EE_REFERER_URL)
    html_form, form_url = _open_with_relogon(opener, req)
    if is_auth_fail(html_form, form_url):
        raise CpisAuthError("EE 驗證失敗（取表單）")

    post = {
        "__VIEWSTATE": extract_input(html_form, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": extract_input(html_form, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": extract_input(html_form, "__EVENTVALIDATION"),
        "txtStart_date": start_date,
        "txtEnd_date": end_date,
        "ddl_shift": "None",
        "ddl_floor": "A2",
        "ddl_etag": ee_etag,
        "dllDept": "None",
        "ddl_bd_id": "None",
        "oper_type": "WD",
        "txt_oper_type": "0",
        "txtJobCode": "",
        "txtEngineer": "",
        "txtAssyLot": "",
        "txtProduct": "",
        "btnFetch": "Fetch",
    }
    post.update(_ee_oper_fields(html_form, ee_entity))

    data = urllib.parse.urlencode(post).encode("utf-8")
    req2 = urllib.request.Request(EE_FRAME_URL, data=data, method="POST")
    req2.add_header("Content-Type", "application/x-www-form-urlencoded")
    req2.add_header("Referer", EE_FRAME_URL)
    html_result, final_url = _open_with_relogon(opener, req2, timeout=60)

    if is_auth_fail(html_result, final_url):
        raise CpisAuthError("EE POST 驗證失敗")

    return _first_matching_table(html_result, EE_TABLE_IDS, filled=True)


# ---------------------------------------------------------------------------
# 7. Utilization Analysis 查詢
# ---------------------------------------------------------------------------

def _utilization_data_url(start_date: str, end_date: str, util_oper: str) -> str:
    return (
        UTIL_BASE + UTIL_DATA_PATH +
        "?sort_by=&ismfgmc=&HidCount=&paging=False&shift=None"
        f"&start_date={start_date}&end_date={end_date}"
        f"&operation={urllib.parse.quote(util_oper)}"
        "&types=p&isinlinecombine=&bd_id=None&getqty=N&sort=None&pline="
    )


def fetch_utilization(
    start_date: str,
    end_date: str,
    util_oper: str = "DA",
    max_frame_depth: int = 2,
) -> list[list[str]]:
    """走 APG 子系統獨立登入查詢 Utilization Analysis，登入成功會直接 redirect 到資料頁。
    回傳的 rows 已把多個候選表格 id 合併、rowspan 展開；若主頁面找不到表格會遞迴掃描
    內嵌 iframe。"""
    cfg = config.load()
    config.require(cfg, "util_user", "util_password")

    data_url = _utilization_data_url(start_date, end_date, util_oper)
    login_url = UTIL_BASE + "/APG/Logon.aspx?ReturnUrl=" + urllib.parse.quote(
        data_url.replace(UTIL_BASE, ""), safe=""
    )

    opener = build_opener()
    login_html, _ = _read(opener, login_url, timeout=20)

    payload = urllib.parse.urlencode(
        {
            "__VIEWSTATE": extract_input(login_html, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": extract_input(login_html, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": extract_input(login_html, "__EVENTVALIDATION"),
            "UserName": cfg["util_user"],
            "Password": cfg["util_password"],
            "Login.x": "61",
            "Login.y": "5",
        }
    ).encode("utf-8")
    req = urllib.request.Request(login_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", login_url)
    html, final_url = _open_with_relogon(opener, req, timeout=60)

    if is_auth_fail(html, final_url):
        raise CpisAuthError("Utilization 驗證失敗")

    return _scan_utilization_tables(opener, html, final_url, max_frame_depth)


def _scan_utilization_tables(opener, html: str, url: str, depth: int) -> list[list[str]]:
    """多表格合併：把每個候選 table id 找到的 rows 都接起來；找不到表格且頁面帶
    iframe 時遞迴掃描 iframe（處理 CPIS 舊版把資料放進內嵌 frame 的情況）。"""
    merged: list[list[str]] = []
    for table_id in UTIL_TABLE_IDS:
        merged.extend(parse_table_filled(html, table_id))

    if merged or depth <= 0:
        return merged

    for src in _iframe_srcs(html):
        frame_url = urllib.parse.urljoin(url, src)
        try:
            frame_html, frame_final_url = _open_with_relogon(opener, frame_url, timeout=30)
        except Exception:
            continue
        if is_auth_fail(frame_html, frame_final_url):
            continue
        rows = _scan_utilization_tables(opener, frame_html, frame_final_url, depth - 1)
        if rows:
            return rows

    return merged
