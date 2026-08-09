"""
CPIS EE Maintenance Record 自動抓取腳本(改版：cpis_api HTTP請求，取代Selenium附身模式)

原本靠附身模式Edge(--remote-debugging-port=9222)操作已登入的分頁，長期卡在
「除錯模式Edge開不起來」的環境問題。改用cpis_api.py純用urllib.request發HTTP
請求登入+查詢，不再需要開瀏覽器。

用法:
    python cpis_scraper.py 20260716 20260717
    python cpis_scraper.py 20260716 20260717 B*
(參數: 起始日期、結束日期，格式YYYYMMDD；不帶參數則預設抓昨天到今天；
 第三個參數是entity萬用字元查詢，預設"*")

前置:
    da_bot資料夾下要有 config.txt(複製 config.txt.example 改名，填入
    apg_user/apg_password)。

已知的欄位結構(F12手動確認過，跟Selenium版一致，只是資料來源換成HTTP回應)：
- 查詢結果表格解析邏輯(parse_result_table)、col_N到DB欄位的對應
  (save_to_db)完全沿用Selenium版，沒有改動。
"""
import sys
import os
import sqlite3
import datetime
from bs4 import BeautifulSoup

import cpis_api

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "da_maintenance.db")


def parse_result_table(html):
    """
    解析查詢結果頁裡的結果表格,回傳 list of dict。
    優先用同事crawler.py已驗證可行的table id(ContentPlaceHolder1_gvData/gvData)去抓，
    抓不到才退回原本Selenium版「挑列數最多的table」heuristic當備援(原始HTTP回應
    可能夾帶版面用的table，用biggest-table heuristic在0筆結果時容易挑錯表格)。
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="ContentPlaceHolder1_gvData") or soup.find("table", id="gvData")
    if table is None:
        tables = soup.find_all("table")
        if not tables:
            return []
        table = max(tables, key=lambda t: len(t.find_all("tr")))
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []
    raw_headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
    # 標題儲存格是空白(常見於圖示/展開按鈕欄位)時,補上位置編號避免欄位名稱撞在一起
    headers = []
    seen = {}
    for i, h in enumerate(raw_headers):
        name = h if h else f"col_{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        headers.append(name)

    records = []
    for tr in rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) == len(headers):
            records.append(dict(zip(headers, cells)))
    return records


def _split_datetime(s):
    """把 '2026/07/16 07:01' 這種字串拆成 (date, time)"""
    s = (s or "").strip()
    if not s:
        return None, None
    parts = s.split(" ", 1)
    date_part = parts[0].replace("/", "-")
    time_part = parts[1] if len(parts) > 1 else None
    return date_part, time_part


def _to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def save_to_db(records):
    """
    把抓到的資料存進SQLite,對應到跟query_bot.py共用的ee_maintenance_record結構。
    col_N 對應關係是根據實際抓回的樣本資料比對出來的:
      col_0=Production Line, col_1=Machine ID,
      col_2=WAIT-TIME, col_3=BGN-TIME, col_4=END-TIME,
      col_5=WAIT DUR, col_6=DUR, col_7=Engineer ID,
      col_8=E-TAG, col_9=JOB CODE, col_10=Cause, col_11/12=Owner/Tool,
      col_13=CON.LOT NO., col_14=STD, col_16=BD_ID, col_17=PRODUCT
    如果某批資料欄位對不上,把印出來的樣本截圖給我微調對應。
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

    n = 0
    for r in records:
        wait_date, wait_time = _split_datetime(r.get("col_2"))
        bgn_date, bgn_time = _split_datetime(r.get("col_3"))
        end_date, end_time = _split_datetime(r.get("col_4"))

        cur.execute("""
            INSERT INTO ee_maintenance_record
            (prod_line, machine_id, wait_date, wait_time, bgn_date, bgn_time,
             end_date, end_time, wait_dur, dur, engineer_id, e_tag, job_code,
             owner, tool_number, cause, con_lot_no, std, bd_id, product)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r.get("col_0"), r.get("col_1"),
            wait_date, wait_time, bgn_date, bgn_time, end_date, end_time,
            _to_float(r.get("col_5")), _to_float(r.get("col_6")),
            r.get("col_7"), r.get("col_8"), r.get("col_9"),
            r.get("col_11"), r.get("col_12"), r.get("col_10"),
            r.get("col_13"), _to_float(r.get("col_14")),
            r.get("col_16"), r.get("col_17"),
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

    entity_pattern = sys.argv[3] if len(sys.argv) >= 4 else "*"

    print(f"查詢區間: {date_start} ~ {date_end}, Entity範圍: {entity_pattern}")

    try:
        html = cpis_api.fetch_ee_maintenance_html(date_start, date_end, entity_pattern)
    except cpis_api.CpisAuthError as e:
        print(f"[錯誤] {e}")
        sys.exit(1)

    records = parse_result_table(html)
    print(f"共擷取 {len(records)} 筆")
    save_to_db(records)
