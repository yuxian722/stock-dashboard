"""
CPIS EE Maintenance Record 自動抓取腳本(改版：cpis_api HTTP請求，取代Selenium附身模式)

原本靠附身模式Edge(--remote-debugging-port=9222)操作已登入的分頁，長期卡在
「除錯模式Edge開不起來」的環境問題。改用cpis_api.py純用urllib.request發HTTP
請求登入+查詢，不再需要開瀏覽器。

用法:
    python cpis_scraper.py 20260716 20260717
    python cpis_scraper.py 20260716 20260717 BA*
(參數: 起始日期、結束日期，格式YYYYMMDD；不帶參數則預設抓昨天到今天；
 第三個參數是entity萬用字元查詢，預設"BA*"，扣掉萬用字元(*/?)後建議至少要有
 2個字元，例如用"BA*"而不是"B*")

前置:
    da_bot資料夾下要有 config.txt(複製 config.txt.example 改名，填入
    apg_user/apg_password)。另外需要 xlrd 套件解析CPIS報表(舊版Excel/BIFF格式)，
    見 requirements_scraper.txt。

資料來源(改版)：cpis_api.fetch_ee_maintenance_xls() 走的是
maintenance_record_r.aspx這個「report產生端點」，回傳的是CPIS產生的
EJP_*.xls報表檔，不是HTML表格，所以這裡改用xlrd解析Excel，不再用
BeautifulSoup解析HTML表格。欄位對照(0-indexed)是實測驗證過的既有dashboard
留下的紀錄：
  0=ProdLine,1=OPER,2=MODEL,3=MACHINE,4=WAIT_DATE,5=WAIT_TIME,
  6=BGN_DATE,7=BGN_TIME,8=END_DATE,9=END_TIME,10=WAIT_DUR,11=DUR,
  12-14=ENG1/ENG2/ENG3(交接班用，優先取最後一個有值的子欄位),
  15=ETAG,16=JOBCODE,19=OWNER,20=TOOL,21=CAUSE,22=DESC,23=LOT,24=STD,
  26=BD_ID,27=PRODUCT
"""
import sys
import os
import re
import sqlite3
import datetime
from bs4 import BeautifulSoup

import cpis_api

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "da_maintenance.db")


def _fmt_date(v):
    """把Excel的日期儲存格值(datetime或字串)轉成'YYYY-MM-DD'，轉不了回傳None。"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, str):
        for fmt in ("%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(v, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def _fmt_time(v):
    """把Excel的時間儲存格值(time/datetime或字串)轉成'HH:MM'，轉不了回傳None。"""
    if v is None or v == "":
        return None
    if isinstance(v, (datetime.time, datetime.datetime)):
        return v.strftime("%H:%M")
    if isinstance(v, str):
        parts = v.split(":")
        if len(parts) >= 2:
            try:
                return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
            except ValueError:
                return None
    return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cell_str(v):
    """Excel儲存格轉成字串；整數值的float(例如123.0)去掉小數點；空白/None回傳None。"""
    if v is None:
        return None
    if isinstance(v, float) and v == int(v):
        v = int(v)
    s = str(v).strip()
    return s if s and s not in ("None", "nan") else None


def _xls_bytes_to_rows(raw_bytes):
    """用xlrd把CPIS報表(.xls，舊版BIFF格式)讀成list of list(儲存格已轉成Python值)。"""
    import xlrd

    wb = xlrd.open_workbook(file_contents=raw_bytes)
    ws = wb.sheet_by_index(0)

    rows = []
    for i in range(ws.nrows):
        row = []
        for j in range(ws.ncols):
            cell = ws.cell(i, j)
            if cell.ctype == xlrd.XL_CELL_DATE:
                t = xlrd.xldate_as_tuple(cell.value, wb.datemode)
                if t[0] > 0:
                    row.append(datetime.datetime(*t))
                else:
                    row.append(datetime.time(t[3], t[4], t[5]))
            else:
                row.append(cell.value)
        rows.append(row)
    return rows


def _rows_to_records(rows):
    """把_xls_bytes_to_rows()讀出的原始列資料轉成list of dict(純邏輯，不碰檔案I/O)。"""
    header_row = 3  # CPIS報表預設表頭在第4列(index 3)
    for i, row in enumerate(rows[:8]):
        cells = [str(c).upper() if c else "" for c in row]
        if any("MACHINE" in c or "BGN" in c for c in cells):
            header_row = i
            break

    def g(row, idx):
        return row[idx] if idx < len(row) else None

    records = []
    for row in rows[header_row + 1:]:
        machine_id = _cell_str(g(row, 3))
        if not machine_id or machine_id.upper() in ("MACHINE", "MACHINE ID"):
            continue

        records.append({
            "prod_line": _cell_str(g(row, 0)),
            "oper": _cell_str(g(row, 1)),
            "model": _cell_str(g(row, 2)),
            "machine_id": machine_id,
            "wait_date": _fmt_date(g(row, 4)),
            "wait_time": _fmt_time(g(row, 5)),
            "bgn_date": _fmt_date(g(row, 6)),
            "bgn_time": _fmt_time(g(row, 7)),
            "end_date": _fmt_date(g(row, 8)),
            "end_time": _fmt_time(g(row, 9)),
            "wait_dur": _to_float(g(row, 10)),
            "dur": _to_float(g(row, 11)),
            # 工程師編號有ENG1/ENG2/ENG3三個子欄位(交接班時前後手可能不同)，
            # 改機分析要看誰真正把工作做完，優先取最後一個有值的子欄位
            "engineer_id": _cell_str(g(row, 14)) or _cell_str(g(row, 13)) or _cell_str(g(row, 12)),
            "e_tag": _cell_str(g(row, 15)),
            "job_code": _cell_str(g(row, 16)),
            "owner": _cell_str(g(row, 19)),
            "tool_number": _cell_str(g(row, 20)),
            "cause": _cell_str(g(row, 21)),
            "description": _cell_str(g(row, 22)),
            "con_lot_no": _cell_str(g(row, 23)),
            "std": _to_float(g(row, 24)),
            "bd_id": _cell_str(g(row, 26)),
            "product": _cell_str(g(row, 27)),
        })

    return records


def parse_ee_maintenance_xls(raw_bytes):
    """解析CPIS EE Maintenance報表(EJP_*.xls)，回傳list of dict。"""
    return _rows_to_records(_xls_bytes_to_rows(raw_bytes))


# ---------------------------------------------------------------------------
# 有Shift篩選功能的查詢表單(maintenance_record_h.aspx)結果HTML解析
# (2026/08/12使用者要求：cpis_api.fetch_ee_maintenance_xls()走的
# maintenance_record_r.aspx，shift查詢參數實測沒有真正被伺服器套用，
# 改用這個真正有Shift下拉選單的表單頁面，回傳的是HTML表格不是XLS)
# ---------------------------------------------------------------------------

_SHIFT_TABLE_REQUIRED_HEADERS = {"MACHINEID", "ENDTIME", "ENGINEERID", "ETAG", "JOBCODE"}


def _normalize_header(text):
    """表頭文字正規化成只留大寫英數字，避免"ENGINEER ID."(含空格句點)
    這種寫法比對失敗。"""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _extract_hhmm(text):
    """從"2026/08/11 09:17"這種日期+時間合併文字擷取"09:17"，抓不到回傳None。"""
    if not text:
        return None
    m = re.search(r"(\d{1,2}:\d{2})", text)
    return m.group(1) if m else None


def _first_token(text):
    """工號欄位實測會顯示成"26163 26163"這種重複兩次的寫法(比照XLS版
    ENG1/ENG2/ENG3子欄位相同時的顯示邏輯)，取第一個空白分隔的token即可。"""
    if not text:
        return None
    parts = text.split()
    return parts[0] if parts else None


def parse_ee_maintenance_shift_html(html):
    """
    解析maintenance_record_h.aspx查詢表單按下Fetch後回傳的結果HTML表格，
    回傳list of dict，欄位是parse_ee_maintenance_xls()那份dict的相容子集：
    machine_id/job_code/engineer_id/dur/wait_dur/end_time/e_tag——
    shift_query.py只需要這幾個欄位；這個HTML表格版面沒有BD_ID/PRODUCT
    這兩欄，跟XLS報表不是同一份完整資料，不能拿來做machine_changeover_
    detail_reply()那種需要Product/B-D欄位的查詢。

    用BeautifulSoup抓所有<table>，白名單制(表頭要同時包含MACHINE ID/
    END-TIME/ENGINEER ID./E.TAG/JOB.CODE，正規化後比對)排除頁面上其他
    無關表格(查詢表單本身等)，比照cpis_utilization_scraper.py的作法。
    """
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        raw_headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        norm_headers = [_normalize_header(h) for h in raw_headers]
        if not _SHIFT_TABLE_REQUIRED_HEADERS.issubset(set(norm_headers)):
            continue

        idx = {name: i for i, name in enumerate(norm_headers)}

        def cell(cells, name):
            i = idx.get(name)
            return cells[i] if i is not None and i < len(cells) else None

        records = []
        for tr in rows[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) != len(norm_headers):
                continue

            machine_id = cell(cells, "MACHINEID")
            if not machine_id:
                continue

            records.append({
                "machine_id": machine_id,
                "end_time": _extract_hhmm(cell(cells, "ENDTIME")),
                "wait_dur": _to_float(cell(cells, "WAITDUR")),
                "dur": _to_float(cell(cells, "DUR")),
                "engineer_id": _first_token(cell(cells, "ENGINEERID")),
                "e_tag": cell(cells, "ETAG") or None,
                "job_code": cell(cells, "JOBCODE") or None,
            })
        return records  # 找到資料表格就直接回傳，不用繼續掃描其他<table>

    return []


def save_to_db(records, date_start=None, date_end=None):
    """
    把抓到的資料存進SQLite,對應到跟query_bot.py共用的ee_maintenance_record結構。

    重要：run_pipeline.py每小時都重抓「昨天~今天」這個有重疊的查詢區間，
    這裡以前只有INSERT、從來沒有DELETE，導致同一筆真實紀錄每小時都被
    重複塞進資料庫一次，累積下來會讓「今日改機統計」這類依日期彙總的
    查詢數字暴增到離譜的程度(實測過EPOXY改機次數膨脹到2645次)。
    這裡改成：如果有帶date_start/date_end(YYYYMMDD)，先刪掉這個查詢
    區間內已經存在的舊資料，再插入這次抓到的新資料，讓同一區間重複
    抓取時是「覆蓋」而不是「疊加」，之後重抓同一天不會再重複計入。
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ee_maintenance_record (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prod_line TEXT, oper TEXT, model TEXT, machine_id TEXT,
        wait_date TEXT, wait_time TEXT, bgn_date TEXT, bgn_time TEXT,
        end_date TEXT, end_time TEXT, wait_dur REAL, dur REAL,
        engineer_id TEXT, e_tag TEXT, job_code TEXT, owner TEXT,
        tool_number TEXT, cause TEXT, description TEXT, con_lot_no TEXT,
        std REAL, bd_id TEXT, product TEXT,
        fetched_at TEXT DEFAULT (datetime('now'))
    )
    """)
    conn.commit()

    if date_start and date_end:
        ds = f"{date_start[:4]}-{date_start[4:6]}-{date_start[6:8]}"
        de = f"{date_end[:4]}-{date_end[4:6]}-{date_end[6:8]}"
        cur.execute("""
            DELETE FROM ee_maintenance_record
            WHERE (bgn_date BETWEEN ? AND ?)
               OR (bgn_date IS NULL AND wait_date BETWEEN ? AND ?)
        """, (ds, de, ds, de))
        conn.commit()

    n = 0
    for r in records:
        cur.execute("""
            INSERT INTO ee_maintenance_record
            (prod_line, oper, model, machine_id, wait_date, wait_time, bgn_date, bgn_time,
             end_date, end_time, wait_dur, dur, engineer_id, e_tag, job_code,
             owner, tool_number, cause, description, con_lot_no, std, bd_id, product)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r.get("prod_line"), r.get("oper"), r.get("model"), r.get("machine_id"),
            r.get("wait_date"), r.get("wait_time"), r.get("bgn_date"), r.get("bgn_time"),
            r.get("end_date"), r.get("end_time"), r.get("wait_dur"), r.get("dur"),
            r.get("engineer_id"), r.get("e_tag"), r.get("job_code"),
            r.get("owner"), r.get("tool_number"), r.get("cause"), r.get("description"),
            r.get("con_lot_no"), r.get("std"), r.get("bd_id"), r.get("product"),
        ))
        n += 1

    conn.commit()
    print(f"[完成] 已寫入 {n} 筆到 {DB_PATH} 的 ee_maintenance_record 資料表")
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        date_start, date_end = sys.argv[1], sys.argv[2]
    else:
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        date_start = yesterday.strftime("%Y%m%d")
        date_end = today.strftime("%Y%m%d")

    entity_pattern = sys.argv[3] if len(sys.argv) >= 4 else "BA*"

    print(f"查詢區間: {date_start} ~ {date_end}, Entity範圍: {entity_pattern}")

    try:
        xls_chunks = cpis_api.fetch_ee_maintenance_xls(date_start, date_end, entity_pattern)
    except (cpis_api.CpisAuthError, ValueError) as e:
        print(f"[錯誤] {e}")
        sys.exit(1)

    records = []
    for raw in xls_chunks:
        records.extend(parse_ee_maintenance_xls(raw))

    print(f"共擷取 {len(records)} 筆")
    save_to_db(records, date_start, date_end)
