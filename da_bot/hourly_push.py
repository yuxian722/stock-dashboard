"""
DA 整點推播 - 訊息產生器 (過渡版，先印出文字供手動複製貼上)
用法: python3 hourly_push.py

之後要接上 team+ API 時，只需要把 main() 最後的 print(msg)
換成呼叫 API 的函式（例如 send_to_teamplus(msg)），
其他組訊息的邏輯都不用改。
"""
import sqlite3
import datetime

DB_PATH = "da_maintenance.db"

# 標準工時對照表(與 query_bot.py 保持一致)
JOB_CODE_STD_HOURS = {
    "CED": 2.3,
    "CE": 3.0,
    "CEE": 3.0,
}


def get_std_hours(job_code: str):
    if not job_code:
        return None
    if job_code in JOB_CODE_STD_HOURS:
        return JOB_CODE_STD_HOURS[job_code]
    for prefix, std in JOB_CODE_STD_HOURS.items():
        if job_code.startswith(prefix):
            return std
    return None


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_ongoing_records():
    """
    抓「進行中」的紀錄：end_date/end_time 為空(還沒結束)
    只看 e_tag = R(修機)或 S(改機)，其他分類(保養/工程異常/品保)不列入超時機台清單
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT machine_id, bgn_date, bgn_time, job_code, e_tag, engineer_id, cause
        FROM ee_maintenance_record
        WHERE (end_date IS NULL OR end_date = '' OR end_time IS NULL OR end_time = '')
          AND e_tag IN ('R', 'S')
          AND bgn_date IS NOT NULL AND bgn_time IS NOT NULL
        ORDER BY bgn_date, bgn_time
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def elapsed_hours(bgn_date: str, bgn_time: str, now: datetime.datetime) -> float:
    """計算從 bgn_date+bgn_time 到現在經過的小時數"""
    try:
        bgn_dt = datetime.datetime.strptime(f"{bgn_date} {bgn_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return 0.0
    delta = now - bgn_dt
    return delta.total_seconds() / 3600.0


def _to_float_percent(s):
    """把 '54.5 %' 這種字串轉成浮點數 54.5，轉不了回傳None"""
    try:
        return float(str(s).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# 整點推播要列出的機型分組，對應CPIS Utilization Analysis頁面最下方「GROUP」
# 官方彙總表(跟query_bot.OFFICIAL_GROUP_LABELS共用同一份官方名稱/大小寫)
PUSH_GROUP_LABELS = ["EPOXY(DB)", "Epoxy", "DB700", "DB800", "DB830", "2100SD"]


def get_official_group_rates():
    """
    從 utilization_record 抓最新一批資料裡，PUSH_GROUP_LABELS這幾個官方GROUP
    彙總列(ENTITY為空、MODEL=群組名)的UTIL/SETUP，回傳
    {group_label: {"util":.., "setup":..}}；抓不到資料的分組不會出現在結果裡，
    整批都沒抓到資料時回傳空dict。
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT MAX(fetched_at) FROM utilization_record")
    row = cur.fetchone()
    latest_fetched_at = row[0] if row else None
    if not latest_fetched_at:
        conn.close()
        return {}

    result = {}
    for label in PUSH_GROUP_LABELS:
        cur.execute("""
            SELECT UTIL, SETUP FROM utilization_record
            WHERE fetched_at = ? AND MODEL = ? AND (ENTITY IS NULL OR ENTITY = '')
        """, (latest_fetched_at, label))
        r = cur.fetchone()
        if r is None:
            continue
        util = _to_float_percent(r["UTIL"])
        if util is None:
            continue
        result[label] = {"util": util, "setup": _to_float_percent(r["SETUP"])}

    conn.close()
    return result


def build_hourly_push_message(now: datetime.datetime = None) -> str:
    """組出整點推播訊息文字(格式比照同事的推播範本)"""
    if now is None:
        now = datetime.datetime.now()

    rows = get_ongoing_records()

    setup_lines = []   # 改機中 (e_tag S)
    repair_lines = []  # 修機中 (e_tag R)

    for r in rows:
        hrs = elapsed_hours(r["bgn_date"], r["bgn_time"], now)
        std = get_std_hours(r["job_code"])

        if std is not None:
            if hrs > std:
                status_note = f"(超時{hrs - std:.2f}hr)"
            else:
                status_note = f"(剩餘{std - hrs:.2f}hr)"
        else:
            status_note = ""

        engineer = r["engineer_id"] or "未指定"
        cause = r["cause"] or "無"
        line = f"{r['machine_id']}  {hrs:.2f}hr{status_note}  {r['job_code']}  工程師:{engineer}  原因:{cause}"

        if r["e_tag"] == "S":
            setup_lines.append(line)
        elif r["e_tag"] == "R":
            repair_lines.append(line)

    title = f"【APG DA 整點推播】{now.strftime('%m/%d %H:%M')}"
    parts = [title, "", "【超時機台】"]

    if setup_lines:
        parts.append("改機中:")
        parts.extend(setup_lines)
    if repair_lines:
        parts.append("修機中:")
        parts.extend(repair_lines)
    if not setup_lines and not repair_lines:
        parts.append("(目前無進行中的改機/修機紀錄)")

    # 稼動/改機 rate，來源是CPIS APG Utilization Analysis頁面最下方的官方GROUP彙總表
    parts.append("")
    parts.append("【稼動 / 改機】")
    group_rates = get_official_group_rates()
    if not group_rates:
        parts.append("(rates 暫無，尚未抓取Utilization Analysis資料)")
    else:
        for label in PUSH_GROUP_LABELS:
            g = group_rates.get(label)
            if g is None:
                parts.append(f"{label}: 暫無資料")
                continue
            line = f"{label}  稼動{g['util']:.1f}%"
            if g["setup"] is not None:
                line += f"  改機{g['setup']:.1f}%"
            parts.append(line)

    return "\n".join(parts)


def send_to_teamplus(msg: str):
    """
    佔位函式：之後拿到 team+ API/Webhook 資訊後，
    在這裡改成真正呼叫 API 的程式碼即可，其他部分都不用動。
    """
    raise NotImplementedError("尚未串接 team+ API，請先用手動複製貼上的方式")


if __name__ == "__main__":
    msg = build_hourly_push_message()
    print(msg)
    print("\n[提示] 目前是過渡版，請手動複製上面的文字貼到 team+ 群組")
    print("[提示] 之後拿到 API 資訊後，把 main 區塊改成呼叫 send_to_teamplus(msg) 即可")
