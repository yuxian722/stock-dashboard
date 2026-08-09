"""
一次性診斷小工具：dedupe_ee_maintenance.py清完之後仍顯示「沒有重複紀錄」，
但今日改機統計數字還是明顯異常暴增(例如274台)時，用這支腳本檢查真正原因。

dedupe_ee_maintenance.py是拿17個欄位「完全一模一樣」當重複的判斷標準
(natural key)。但CPIS的EE Maintenance紀錄很可能在同一筆真實改機事件關閉
之後，cause/description/std/owner/tool_number這類輔助欄位還會被工程師
事後補填，導致同一筆真實事件在不同小時抓到的內容不完全一樣——這樣就不會
被判定成「完全重複」，dedupe抓不到，但拿來統計「今日改機次數」用的判斷
標準(machine_id+bgn_date+bgn_time+job_code，見hourly_push.py的
get_epoxy_done_by_jcode())理論上應該還是能把它們收斂成同一筆才對。

這支腳本會列出：
1. 今天(e_tag='S', end_date=今天)的原始筆數，以及分別用幾種不同「唯一性
   判斷標準」算出來的筆數，方便對照到底是哪個環節沒收斂
2. 挑幾台重複次數最多的機台，把每一筆的bgn_time/job_code/cause都印出來，
   肉眼比對到底是哪個欄位在變動導致沒被判定成同一筆

用法: python _inspect_today_setup.py
"""
import sys
import os
import sqlite3
import datetime

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "da_maintenance.db")


def _count(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchone()[0]


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"[錯誤] 找不到資料庫: {DB_PATH}")
        sys.exit(1)

    today = datetime.date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    where = "e_tag = 'S' AND end_date = ?"

    raw_total = _count(cur, f"SELECT COUNT(*) FROM ee_maintenance_record WHERE {where}", (today,))
    distinct_full_key = _count(
        cur,
        f"SELECT COUNT(*) FROM (SELECT DISTINCT machine_id, bgn_date, bgn_time, job_code "
        f"FROM ee_maintenance_record WHERE {where})",
        (today,),
    )
    distinct_no_jobcode = _count(
        cur,
        f"SELECT COUNT(*) FROM (SELECT DISTINCT machine_id, bgn_date, bgn_time "
        f"FROM ee_maintenance_record WHERE {where})",
        (today,),
    )
    distinct_machine_only = _count(
        cur,
        f"SELECT COUNT(*) FROM (SELECT DISTINCT machine_id "
        f"FROM ee_maintenance_record WHERE {where})",
        (today,),
    )
    distinct_fetched_at = _count(
        cur,
        f"SELECT COUNT(DISTINCT fetched_at) FROM ee_maintenance_record WHERE {where}",
        (today,),
    )

    print(f"今天(e_tag=S, end_date={today})的完成改機紀錄:")
    print(f"  原始筆數(完全不去重):                          {raw_total}")
    print(f"  用(機台+bgn_date+bgn_time+job_code)去重後:      {distinct_full_key}  <- hourly_push.py目前用這個當統計數字")
    print(f"  用(機台+bgn_date+bgn_time)去重後(不看job_code): {distinct_no_jobcode}")
    print(f"  只看有出現過的相異機台數:                        {distinct_machine_only}")
    print(f"  資料分布在幾個不同的fetched_at批次:              {distinct_fetched_at}  <- 遠大於1~2代表同一區間被重複寫入很多次，很可能是服務沒有真的重新啟動生效")

    print()
    print("重複次數最多的前10台機台，逐筆列出bgn_time/job_code/cause，方便肉眼比對哪個欄位在變動:")
    cur.execute(f"""
        SELECT machine_id, COUNT(*) as n
        FROM ee_maintenance_record WHERE {where}
        GROUP BY machine_id ORDER BY n DESC LIMIT 10
    """, (today,))
    top_machines = cur.fetchall()
    for row in top_machines:
        mid, n = row["machine_id"], row["n"]
        print(f"\n{mid}  (共{n}筆)")
        cur.execute(f"""
            SELECT bgn_time, job_code, cause, description, std, fetched_at
            FROM ee_maintenance_record WHERE {where} AND machine_id = ?
            ORDER BY bgn_time, fetched_at
        """, (today, mid))
        for r in cur.fetchall():
            print(f"    bgn_time={r['bgn_time']!r}  job_code={r['job_code']!r}  "
                  f"cause={r['cause']!r}  description={r['description']!r}  "
                  f"std={r['std']!r}  fetched_at={r['fetched_at']}")

    conn.close()
