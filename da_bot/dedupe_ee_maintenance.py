"""
一次性清理工具：清掉 da_maintenance.db 的 ee_maintenance_record 資料表裡
既有的重複紀錄。

背景：cpis_scraper.py 的 save_to_db() 以前只有 INSERT、沒有 DELETE，而
run_pipeline.py 每小時都重抓「昨天~今天」這個有重疊的查詢區間，導致同一筆
真實紀錄每小時都被重複塞進資料庫一次，累積下來讓「今日改機統計」這類依日期
彙總的查詢/推播數字暴增到離譜的程度(實測過EPOXY改機次數膨脹到2645次)。
save_to_db() 已經修好、不會再往後累積，但這支腳本執行之前已經寫進資料庫的
重複列不會自動消失，需要手動跑這支腳本清一次(只需要跑一次)。

用法:
    python dedupe_ee_maintenance.py           # 先預覽會刪掉幾筆重複列(不會真的刪)
    python dedupe_ee_maintenance.py --yes     # 確認後才真正刪除

安全性: 執行刪除前會先把 da_maintenance.db 備份成
da_maintenance.db.bak-YYYYMMDDHHMMSS，刪錯了可以拿備份檔復原。
"""
import sys
import os
import shutil
import sqlite3
import datetime

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "da_maintenance.db")

# 拿掉id(每筆自動編號一定不同)跟fetched_at(每次抓取時間不同)之後，
# 剩下這些欄位如果完全一樣，就是同一筆真實紀錄被重複INSERT
_NATURAL_KEY_COLUMNS = [
    "prod_line", "oper", "model", "machine_id",
    "wait_date", "wait_time", "bgn_date", "bgn_time",
    "end_date", "end_time", "wait_dur", "dur",
    "engineer_id", "e_tag", "job_code", "owner",
    "tool_number", "cause", "description", "con_lot_no",
    "std", "bd_id", "product",
]


def find_duplicate_count(conn):
    """回傳(總筆數, 會被刪掉的重複筆數)。"""
    cur = conn.cursor()
    total = cur.execute("SELECT COUNT(*) FROM ee_maintenance_record").fetchone()[0]
    key_cols = ", ".join(_NATURAL_KEY_COLUMNS)
    # SQLite的COUNT(DISTINCT ...)不支援多欄位，改用GROUP BY子查詢算出不重複的組數
    cur.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT 1 FROM ee_maintenance_record GROUP BY {key_cols}
        )
    """)
    distinct_groups = cur.fetchone()[0]
    return total, total - distinct_groups


def dedupe(conn):
    """每組重複紀錄只保留id最小的一筆，其餘刪除。回傳刪掉的筆數。"""
    key_cols = ", ".join(_NATURAL_KEY_COLUMNS)
    cur = conn.cursor()
    cur.execute(f"""
        DELETE FROM ee_maintenance_record
        WHERE id NOT IN (
            SELECT MIN(id) FROM ee_maintenance_record GROUP BY {key_cols}
        )
    """)
    conn.commit()
    return cur.rowcount


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"[錯誤] 找不到資料庫: {DB_PATH}")
        sys.exit(1)

    apply_changes = "--yes" in sys.argv[1:]

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='ee_maintenance_record'
        """)
        if not cur.fetchone():
            print("[提示] ee_maintenance_record 資料表不存在，沒有東西可以清")
            sys.exit(0)

        total, dup = find_duplicate_count(conn)
        print(f"目前 ee_maintenance_record 共 {total} 筆，其中重複紀錄約 {dup} 筆")

        if dup == 0:
            print("[完成] 沒有重複紀錄，不需要清理")
            sys.exit(0)

        if not apply_changes:
            print("\n[預覽模式] 沒有實際刪除任何資料")
            print("確認沒問題後，請加上 --yes 參數重新執行以真正清除重複紀錄:")
            print("    python dedupe_ee_maintenance.py --yes")
            sys.exit(0)

        backup_path = f"{DB_PATH}.bak-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        conn.close()
        shutil.copy2(DB_PATH, backup_path)
        print(f"[備份] 已備份原始資料庫到 {backup_path}")

        conn = sqlite3.connect(DB_PATH)
        removed = dedupe(conn)
        remaining = conn.execute("SELECT COUNT(*) FROM ee_maintenance_record").fetchone()[0]
        print(f"[完成] 已刪除 {removed} 筆重複紀錄，剩下 {remaining} 筆")
        print(f"[提示] 如果清錯了，備份檔在: {backup_path}")
    finally:
        conn.close()
