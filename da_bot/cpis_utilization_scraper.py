"""
CPIS Utilization Analysis 爬蟲 (改版：cpis_api HTTP請求，取代Selenium附身模式)

用法: python cpis_utilization_scraper.py <起始日YYYYMMDD> <結束日YYYYMMDD>

前置:
    da_bot資料夾下要有 config.txt(複製 config.txt.example 改名，填入
    util_user/util_password)。session過期時cpis_api.py會自動重新登入、重試查詢。

備註1: Start Date / End Date 對應CPIS網頁下拉select的選項，僅包含網站上
       已有資料的日期，若指定的日期(尤其是"今天")不在選項裡會直接失敗，
       屬於資料尚未產生，不是程式bug，換一個已知有資料的日期即可

備註2(08/06重大發現，沿用自Selenium版，跟資料來源無關，是CPIS網頁本身的結構特性)：
       靠診斷輸出(print_table_diagnostics)才找到真正結構：
       - 頁面裡混雜著跟稼動率無關的表格(公佈欄異常處理、查詢表單本身)，
         之前的「表頭非空白欄位數>=2」判斷太寬鬆，會把這些也當成資料表格。
         已改為白名單制：表頭必須同時包含MODEL與UTIL才視為資料表格。
       - 所有機型(2100advi/2100FC-I/DB800/DB830/WMI-320...)其實是同一個
         225列的大表格，不是各自獨立的表格。此表格MODEL欄位用rowspan合併
         儲存格，只有每個機型群組的第一列才有MODEL文字，同群組其餘列少了
         這一欄，導致原本「欄位數必須完全對齊」的檢查把它們整批跳過。
         已改為：若某列欄位數剛好比表頭少1(且表頭第一欄是MODEL)，視為缺少
         被rowspan省略的MODEL欄，用同一表格內"前一列記住的MODEL值"補上，
         不再整列捨棄。
"""
import os
import sys
import sqlite3
import datetime
from bs4 import BeautifulSoup

import cpis_api

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "da_maintenance.db")

# 欄位名稱別名對照：不同機型分區的表格，表頭文字可能不一致，
# 但語意相同，需要正規化成同一個欄位名稱
COLUMN_ALIASES = {
    "GROUP": "MODEL",
}

# 小計/目標列的識別值：這些不是真正的機台資料，寫入DB前要過濾掉，
# 否則 db_group_reply() 算平均時會被污染
SUMMARY_ROW_VALUES = {"SUM", "TARGET"}

# 白名單(08/06新增)：表頭必須同時包含這些欄位，才視為稼動率資料表格，
# 排除頁面上其他無關的表格(公佈欄異常處理、查詢表單本身等)
REQUIRED_HEADERS_FOR_DATA_TABLE = {"MODEL", "UTIL"}


def fetch_tables(date_start, date_end, util_oper="DA"):
    """
    透過cpis_api查詢Utilization Analysis，回傳所有候選<table> Tag清單
    (跨cpis_api.fetch_utilization_html()回傳的每個HTML頁面合併，對應原本
    Selenium版collect_all_tables_recursive遞迴掃描所有frame、合併表格的邏輯；
    session過期重試已經在cpis_api.fetch_utilization_html()裡處理過)。
    """
    html_list = cpis_api.fetch_utilization_html(date_start, date_end, util_oper=util_oper)
    tables = []
    for html in html_list:
        soup = BeautifulSoup(html, "html.parser")
        tables.extend(soup.find_all("table"))
    return tables


def _normalize_headers(raw_headers):
    """把原始表頭文字轉成正規化後的欄位名稱清單(處理空白欄位命名、GROUP->MODEL別名、重複欄位命名)"""
    seen = {}
    headers = []
    for i, h in enumerate(raw_headers):
        name = h if h else f"col_{i}"
        name = COLUMN_ALIASES.get(name, name)
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        headers.append(name)
    return headers


def print_table_diagnostics(tables):
    """診斷用：印出每個收集到的表格的基本資訊，方便判斷解析邏輯是否正確"""
    print(f"[診斷] 共收集到 {len(tables)} 個<table>，逐一檢視:")
    for i, table in enumerate(tables):
        rows = table.find_all("tr")
        if not rows:
            print(f"  #{i}: 0列(空表格)")
            continue
        raw_headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        headers = _normalize_headers(raw_headers)
        is_data_table = REQUIRED_HEADERS_FOR_DATA_TABLE.issubset(set(headers))
        preview = " | ".join(raw_headers[:8])
        if len(raw_headers) > 8:
            preview += " ..."
        status = "資料表格，會解析" if (len(rows) >= 2 and is_data_table) else "非資料表格，會跳過"
        print(f"  #{i}: {len(rows)}列, 第一列({len(raw_headers)}欄)=[{preview}] -> {status}")


def parse_tables(tables):
    """
    解析BeautifulSoup <table> Tag清單，回傳合併後的資料records。

    修正(08/06)：
    1. 白名單制:表頭需同時包含MODEL與UTIL才視為資料表格，排除頁面上
       公佈欄異常處理、查詢表單本身等無關表格
    2. rowspan補值:若某列欄位數剛好比表頭少1(且表頭第一欄是MODEL)，
       視為該列的MODEL儲存格被rowspan省略，用同表格內"前一列記住的
       MODEL值"補上，不再整列捨棄
    3. 過濾SUM/TARGET小計列
    """
    if not tables:
        return []

    all_records = []
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        raw_headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
        headers = _normalize_headers(raw_headers)

        if not REQUIRED_HEADERS_FOR_DATA_TABLE.issubset(set(headers)):
            continue  # 不是稼動率資料表格(例如公佈欄、查詢表單)，跳過

        model_is_first_col = headers and headers[0] == "MODEL"
        last_model = None

        for tr in rows[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]

            if len(cells) == len(headers):
                row_dict = dict(zip(headers, cells))
                if model_is_first_col:
                    last_model = row_dict.get("MODEL", last_model)
            elif model_is_first_col and len(cells) == len(headers) - 1 and last_model is not None:
                # 缺少開頭MODEL欄(被rowspan省略)，用前一列記住的MODEL值補上
                row_dict = dict(zip(headers[1:], cells))
                row_dict["MODEL"] = last_model
            else:
                continue  # 真正無法對齊的列，略過

            model_val = str(row_dict.get("MODEL", "")).strip().upper()
            entity_val = str(row_dict.get("ENTITY", "")).strip().upper()
            if model_val in SUMMARY_ROW_VALUES or entity_val in SUMMARY_ROW_VALUES:
                continue

            all_records.append(row_dict)

    return all_records


def _to_float(s):
    try:
        return float(str(s).replace("%", ""))
    except (TypeError, ValueError):
        return None


def save_to_db(records, date_start, date_end):
    """寫入utilization_record 資料表，動態依所有records出現過的欄位建欄位"""
    if not records:
        print("[提示] 沒有資料可寫入")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    all_keys = set()
    for r in records:
        all_keys.update(r.keys())
    all_keys = sorted(all_keys)

    columns_def = ", ".join(f'"{k}" TEXT' for k in all_keys)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS utilization_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_date_start TEXT,
            query_date_end TEXT,
            {columns_def},
            fetched_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

    n = 0
    for r in records:
        cols = ["query_date_start", "query_date_end"] + all_keys
        col_list_sql = ",".join(f'"{c}"' for c in cols)
        placeholders = ",".join(["?"] * len(cols))
        values = [date_start, date_end] + [r.get(k) for k in all_keys]
        sql = f'INSERT INTO utilization_record ({col_list_sql}) VALUES ({placeholders})'
        cur.execute(sql, values)
        n += 1

    conn.commit()
    print(f"[完成] 寫入{n}筆到 {DB_PATH} 的utilization_record 資料表")
    print(f"[提示] 已排除SUM/TARGET小計列，欄位已正規化(GROUP->MODEL等)")
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        date_start, date_end = sys.argv[1], sys.argv[2]
    else:
        today = datetime.date.today()
        date_start = date_end = today.strftime("%Y%m%d")

    print(f"查詢區間: {date_start} ~ {date_end}")

    try:
        tables = fetch_tables(date_start, date_end)
    except cpis_api.CpisAuthError as e:
        print(f"[錯誤] {e}")
        sys.exit(1)

    print(f"[提示] 共收集到 {len(tables)} 個<table>")
    print_table_diagnostics(tables)
    records = parse_tables(tables)
    print(f"共擷取{len(records)} 筆")
    if records:
        print("[提示] 第一筆原始資料範例:")
        print(records[0])
    save_to_db(records, date_start, date_end)
