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
    "CED-M2": 2.9,  # Multi step(2 dies)，2026/08/09使用者提供
    "CED-M3": 2.9,  # Multi step(3 dies)，2026/08/09使用者提供
    "CED-M4": 2.9,  # Multi step(4 dies)，2026/08/09使用者提供
    "CED": 2.3,     # 頂針(CED-1等)，2026/08/09使用者提供
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
    抓「進行中」的紀錄：bgn_date/bgn_time 已填(已經開始動工)、end_date/end_time
    還是空的(還沒結束)。只看 e_tag = R(修機)或 S(改機)，其他分類(保養/工程異常/
    品保)不列入超時機台清單。
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


def get_waiting_records():
    """
    抓「排隊等待中」的紀錄：wait_date/wait_time已填(已經排入等待)，
    但bgn_date還是空的(還沒真的開始動工)、也還沒結束。
    這批資料本來就存在ee_maintenance_record裡(cpis_scraper.py解析EJP報表時
    wait_date/wait_time欄位就有存)，只是之前的整點推播沒有查詢/顯示過。
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT machine_id, wait_date, wait_time, job_code, e_tag
        FROM ee_maintenance_record
        WHERE wait_date IS NOT NULL AND wait_date != ''
          AND (bgn_date IS NULL OR bgn_date = '')
          AND (end_date IS NULL OR end_date = '')
          AND e_tag IN ('R', 'S')
        ORDER BY wait_date, wait_time
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def elapsed_hours(date_str: str, time_str: str, now: datetime.datetime) -> float:
    """計算從 date_str+time_str 到現在經過的小時數(bgn/wait兩種時間戳記都能用)"""
    try:
        dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return 0.0
    delta = now - dt
    return delta.total_seconds() / 3600.0


# 機台代號→機型群組(ESEC/DB/LOC/FC)，依代號前3碼分類，對齊同事Dashboard的
# getEntityGroup規則(APG_TeamplusBot/teamplus_bot.py的_entity_group())
_ENTITY_GROUP_PREFIXES = {
    "BA2": "ESEC", "BA4": "ESEC",
    "BA7": "DB", "BAA": "DB", "BAB": "DB",
    "BA8": "LOC",
    "BA5": "FC", "FC5": "FC", "BAD": "FC", "FC1": "FC",
}


def _group_for_machine(machine_id):
    p3 = (machine_id or "").upper()[:3]
    return _ENTITY_GROUP_PREFIXES.get(p3)


def get_setup_group_stats():
    """
    今日改機統計，依機型群組(ESEC/DB/LOC/FC)分組。回傳
    {group: {"done": 今日已完成次數, "in_progress": 改機中台數, "waiting": 待改台數}}。
    """
    today = datetime.date.today().isoformat()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT machine_id, wait_date, bgn_date, end_date
        FROM ee_maintenance_record
        WHERE e_tag = 'S'
    """)
    rows = cur.fetchall()
    conn.close()

    stats = {g: {"done": 0, "in_progress": 0, "waiting": 0} for g in ("ESEC", "DB", "LOC", "FC")}
    for r in rows:
        g = _group_for_machine(r["machine_id"])
        if g is None:
            continue
        if r["end_date"]:
            if r["end_date"] == today:
                stats[g]["done"] += 1
        elif r["bgn_date"]:
            stats[g]["in_progress"] += 1
        elif r["wait_date"]:
            stats[g]["waiting"] += 1
    return stats


# PM/REPAIR/SETUP Monitor頁面的STATUS代碼(cpis_pm_monitor_scraper.py抓的
# 即時機況)顯示用中文名稱，跟query_bot.py的_PM_STATUS_ZH對照一致
_PM_STATUS_LABELS = [
    ("IN-REPAIR", "修機中"), ("WAIT-REPAIR", "等待修機"),
    ("SETUP", "改機中"), ("WAIT-SETUP", "等待改機"),
    ("PM", "保養中"), ("ENG", "工程異常"),
]


def get_pm_monitor_group_stats():
    """
    從pm_monitor_record最新一批快照(cpis_pm_monitor_scraper.py抓的PM/REPAIR/
    SETUP Monitor即時機況)依機型群組(ESEC/DB/LOC/FC)統計各STATUS台數，是CPIS
    當下真正的異常機況清單，不是像get_setup_group_stats()那樣從EE Maintenance
    歷史紀錄推算的近似值。回傳{group: {status_code: 台數}}；這個資料表用
    Selenium無頭瀏覽器抓，比較容易受環境影響、可能還沒抓過，資料表不存在或
    是空的時候回傳空dict(呼叫端要優雅跳過，不能讓整個推播訊息掛掉)。
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT MAX(fetched_at) FROM pm_monitor_record")
    except sqlite3.OperationalError:
        conn.close()
        return {}
    row = cur.fetchone()
    latest = row[0] if row else None
    if not latest:
        conn.close()
        return {}

    cur.execute("SELECT entity, status FROM pm_monitor_record WHERE fetched_at = ?", (latest,))
    rows = cur.fetchall()
    conn.close()

    stats = {g: {} for g in ("ESEC", "DB", "LOC", "FC")}
    for r in rows:
        g = _group_for_machine(r["entity"])
        if g is None:
            continue
        stats[g][r["status"]] = stats[g].get(r["status"], 0) + 1
    return stats


def _pm_stats_line(label, group_stats, indent=""):
    field_parts = [f"{zh}{group_stats[code]}" for code, zh in _PM_STATUS_LABELS if group_stats.get(code)]
    if not field_parts:
        return None
    return f"{indent}{label}  " + " ".join(field_parts)


def _to_float_percent(s):
    """把 '54.5 %' 這種字串轉成浮點數 54.5，轉不了回傳None"""
    try:
        return float(str(s).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# 整點推播要列出的機型分組，對應CPIS Utilization Analysis頁面最下方「GROUP」
# 官方彙總表(跟query_bot.OFFICIAL_GROUP_LABELS共用同一份官方名稱/大小寫)
PUSH_GROUP_LABELS = ["EPOXY(DB)", "Epoxy", "DB700", "DB800", "DB830", "2100SD"]

# 推播每個分組要列出哪些欄位(DB欄位名, 顯示用名稱)，對應CPIS GROUP彙總表的欄位
PUSH_GROUP_FIELDS = [
    ("UTIL", "稼動"),
    ("W-SET", "等待改機"),
    ("SETUP", "改機"),
    ("ENG", "工程"),
    ("PM", "保養"),
    ("W-REP", "等待修機"),
    ("IN-REP", "修機"),
]


def get_official_group_rates():
    """
    從 utilization_record 抓最新一批資料裡，PUSH_GROUP_LABELS這幾個官方GROUP
    彙總列(ENTITY為空、MODEL=群組名)的PUSH_GROUP_FIELDS各欄位數字，回傳
    {group_label: {欄位名: 數值或None}}；抓不到資料的分組不會出現在結果裡，
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

    field_cols = ", ".join(f'"{col}"' for col, _ in PUSH_GROUP_FIELDS)
    result = {}
    for label in PUSH_GROUP_LABELS:
        cur.execute(f"""
            SELECT {field_cols} FROM utilization_record
            WHERE fetched_at = ? AND MODEL = ? AND (ENTITY IS NULL OR ENTITY = '')
        """, (latest_fetched_at, label))
        r = cur.fetchone()
        if r is None:
            continue
        values = {col: _to_float_percent(r[col]) for col, _ in PUSH_GROUP_FIELDS}
        if values["UTIL"] is None:
            continue
        result[label] = values

    conn.close()
    return result


def _setup_stats_line(label, s, indent=""):
    return f"{indent}{label}   改機{s['done']} | 改機中{s['in_progress']} | 待改{s['waiting']}"


def _overtime_line(r, hrs, show_cause=False):
    """
    超時機台清單裡的一行(改機中/修機中共用)：機台 經過時數(超時Xhr) 代碼 人員xxx。
    改機沒有「原因」這種概念(排定的正常換線，不是故障)，只有修機才顯示
    原因(show_cause=True)，沿用之前"修機過久要看修機人員/修機內容"的需求。
    """
    std = get_std_hours(r["job_code"])
    status_note = f"(超時{hrs - std:.2f}hr)" if (std is not None and hrs > std) else ""
    engineer = r["engineer_id"] or "未指定"
    line = f"{r['machine_id']}  {hrs:.2f}hr{status_note}  {r['job_code']}  人員{engineer}"
    if show_cause:
        cause = r["cause"] or "無"
        line += f"  原因:{cause}"
    return line


def _waiting_line(r, hrs, label):
    return f"{r['machine_id']}  {label}{hrs:.2f}hr  {r['job_code']}"


def build_hourly_push_message(now: datetime.datetime = None) -> str:
    """組出整點推播訊息文字(格式比照同事的推播範本)"""
    if now is None:
        now = datetime.datetime.now()

    title = f"【APG DA 整點推播】{now.strftime('%m/%d %H:%M')}"
    parts = [title]

    # 🔧 今日改機統計：依機型群組(EPOXY=ESEC+DB、LOC、FlipChip)列出今日已完成/
    # 改機中/待改的台數，wait_date這些欄位其實資料庫裡早就有存，只是之前的
    # 整點推播沒有查詢/顯示過
    setup_stats = get_setup_group_stats()
    esec, db, loc, fc = setup_stats["ESEC"], setup_stats["DB"], setup_stats["LOC"], setup_stats["FC"]
    epoxy = {k: esec[k] + db[k] for k in ("done", "in_progress", "waiting")}
    parts.append("")
    parts.append("🔧 今日改機統計")
    parts.append(_setup_stats_line("EPOXY", epoxy))
    parts.append(_setup_stats_line("├ESEC", esec, " "))
    parts.append(_setup_stats_line("└DB", db, " "))
    parts.append(_setup_stats_line("LOC", loc))
    parts.append(_setup_stats_line("FlipChip", fc))

    # ⚡ 即時機況：來源是PM/REPAIR/SETUP Monitor頁面的真實快照(不是像上面
    # 「今日改機統計」那樣用EE Maintenance歷史紀錄推算的近似值)。這個資料源
    # 可能還沒抓過或抓取失敗，抓不到資料時整段跳過，不影響其他推播內容
    pm_stats = get_pm_monitor_group_stats()
    if any(pm_stats.get(g) for g in ("ESEC", "DB", "LOC", "FC")):
        epoxy_pm = {}
        for g in ("ESEC", "DB"):
            for code, cnt in pm_stats.get(g, {}).items():
                epoxy_pm[code] = epoxy_pm.get(code, 0) + cnt
        lines = [
            _pm_stats_line("EPOXY", epoxy_pm),
            _pm_stats_line("├ESEC", pm_stats.get("ESEC", {}), " "),
            _pm_stats_line("└DB", pm_stats.get("DB", {}), " "),
            _pm_stats_line("LOC", pm_stats.get("LOC", {})),
            _pm_stats_line("FlipChip", pm_stats.get("FC", {})),
        ]
        lines = [ln for ln in lines if ln is not None]
        if lines:
            parts.append("")
            parts.append("⚡ 即時機況(PM Monitor)")
            parts.extend(lines)

    # ⏰ 超時機台：改機中/修機中(進行中且超過標準工時) + 待改/待修(還在排隊等待中)
    ongoing = get_ongoing_records()
    waiting = get_waiting_records()

    setup_ongoing = [r for r in ongoing if r["e_tag"] == "S"]
    repair_ongoing = [r for r in ongoing if r["e_tag"] == "R"]
    setup_waiting = [r for r in waiting if r["e_tag"] == "S"]
    repair_waiting = [r for r in waiting if r["e_tag"] == "R"]

    parts.append("")
    parts.append("⏰ 超時機台")
    if setup_ongoing:
        parts.append("🔧改機中")
        for r in setup_ongoing:
            parts.append(_overtime_line(r, elapsed_hours(r["bgn_date"], r["bgn_time"], now)))
    if setup_waiting:
        parts.append("⏳待改")
        for r in setup_waiting:
            parts.append(_waiting_line(r, elapsed_hours(r["wait_date"], r["wait_time"], now), "待改"))
    if repair_ongoing:
        parts.append("🔨修機中")
        for r in repair_ongoing:
            parts.append(_overtime_line(r, elapsed_hours(r["bgn_date"], r["bgn_time"], now), show_cause=True))
    if repair_waiting:
        parts.append("⏳待修")
        for r in repair_waiting:
            parts.append(_waiting_line(r, elapsed_hours(r["wait_date"], r["wait_time"], now), "待修"))
    if not (setup_ongoing or setup_waiting or repair_ongoing or repair_waiting):
        parts.append("(目前無進行中/等待中的改機/修機紀錄)")

    # 稼動/改機 rate，來源是CPIS APG Utilization Analysis頁面最下方的官方GROUP彙總表
    parts.append("")
    parts.append("【稼動 / 改機】")
    group_rates = get_official_group_rates()
    if not group_rates:
        parts.append("(rates 暫無，尚未抓取Utilization Analysis資料)")
    else:
        for label in PUSH_GROUP_LABELS:
            values = group_rates.get(label)
            if values is None:
                parts.append(f"{label}: 暫無資料")
                continue
            field_parts = [
                f"{name}{v:.1f}%" for col, name in PUSH_GROUP_FIELDS
                if (v := values.get(col)) is not None
            ]
            parts.append(f"{label}  " + " ".join(field_parts))

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
