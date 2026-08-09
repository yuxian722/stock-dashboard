"""
DA 設備修機/改機查詢機器人 - 命令列版本

用法: python3 query_bot.py <機台代號> [detail|live|util]
範例: python3 query_bot.py BA205
      python3 query_bot.py BA205 detail   <- 當天明細每一筆
      python3 query_bot.py BA205 live     <- 即時狀態(有沒有進行中)
      python3 query_bot.py BA205 util     <- 最新稼動率
"""
import sqlite3
import sys
import datetime

DB_PATH = "da_maintenance.db"

# 各 JOB CODE 的標準工時(小時)，用來判斷是否超時
# key 是完整 JOB CODE 或前綴(完整對不到就用前綴比對)
JOB_CODE_STD_HOURS = {
    "CED": 2.3,   # 改機標準工時(依經驗值，2026/07/17確認)
    "CE": 3.0,    # 改機標準工時(依經驗值，2026/07/17確認)
    "CEE": 3.0,   # 設備工程 CEE 類改機標準工時
}


def get_std_hours(job_code):
    """依 JOB_CODE 查標準工時，查不到回傳 None"""
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


def summary_reply(machine_id: str, date: str = None) -> str:
    """回傳某天的修機/改機統計摘要，依 Job Code 分類"""
    conn = get_conn()
    cur = conn.cursor()

    if date is None:
        cur.execute("SELECT MAX(bgn_date) FROM ee_maintenance_record WHERE machine_id = ?", (machine_id,))
        row = cur.fetchone()
        date = row[0] if row and row[0] else datetime.date.today().isoformat()

    def job_code_breakdown(e_tag):
        cur.execute("""
        SELECT job_code, COUNT(*), COALESCE(SUM(dur),0), COALESCE(SUM(wait_dur),0)
        FROM ee_maintenance_record
        WHERE machine_id = ? AND bgn_date = ? AND e_tag = ?
        GROUP BY job_code
        ORDER BY COUNT(*) DESC
        """, (machine_id, date, e_tag))
        return cur.fetchall()

    repair_rows = job_code_breakdown("R")
    setup_rows = job_code_breakdown("S")

    conn.close()

    if not repair_rows and not setup_rows:
        return f"{machine_id} ({date})\n當天沒有修機/改機紀錄"

    lines = [f"{machine_id} ({date})"]

    def fmt_section(label, rows):
        if not rows:
            return
        total_cnt = sum(r[1] for r in rows)
        total_dur = sum(r[2] for r in rows)
        total_wait = sum(r[3] for r in rows)
        detail = "、".join(f"{code}x{cnt}" for code, cnt, dur, wait in rows)
        lines.append(
            f"{label}:{detail}(共{total_cnt}筆 / {total_dur:.2f}hr / Wait {total_wait:.2f}hr)"
        )

    fmt_section("修機", repair_rows)
    fmt_section("改機", setup_rows)

    return "\n".join(lines)


def summary_reply_range(machine_id: str, date_start: str, date_end: str) -> str:
    """回傳某期間(date_start~date_end，含頭尾)的修機/改機統計摘要，依 Job Code 分類"""
    conn = get_conn()
    cur = conn.cursor()

    def job_code_breakdown(e_tag):
        cur.execute("""
        SELECT job_code, COUNT(*), COALESCE(SUM(dur),0), COALESCE(SUM(wait_dur),0)
        FROM ee_maintenance_record
        WHERE machine_id = ? AND bgn_date BETWEEN ? AND ? AND e_tag = ?
        GROUP BY job_code
        ORDER BY COUNT(*) DESC
        """, (machine_id, date_start, date_end, e_tag))
        return cur.fetchall()

    repair_rows = job_code_breakdown("R")
    setup_rows = job_code_breakdown("S")

    conn.close()

    if not repair_rows and not setup_rows:
        return f"{machine_id} ({date_start}~{date_end})\n這段期間沒有修機/改機紀錄"

    lines = [f"{machine_id} ({date_start}~{date_end})"]

    def fmt_section(label, rows):
        if not rows:
            return
        total_cnt = sum(r[1] for r in rows)
        total_dur = sum(r[2] for r in rows)
        total_wait = sum(r[3] for r in rows)
        detail = "、".join(f"{code}x{cnt}" for code, cnt, dur, wait in rows)
        lines.append(
            f"{label}:{detail}(共{total_cnt}筆 / {total_dur:.2f}hr / Wait {total_wait:.2f}hr)"
        )

    fmt_section("修機", repair_rows)
    fmt_section("改機", setup_rows)

    return "\n".join(lines)


def detail_reply(machine_id: str, date: str = None, e_tag: str = None) -> str:
    """回傳某天的修機/改機逐筆明細"""
    conn = get_conn()
    cur = conn.cursor()

    if date is None:
        cur.execute("SELECT MAX(bgn_date) FROM ee_maintenance_record WHERE machine_id = ?", (machine_id,))
        row = cur.fetchone()
        date = row[0] if row and row[0] else datetime.date.today().isoformat()

    query = """
        SELECT bgn_time, job_code, e_tag, cause, description, dur, wait_dur, engineer_id
        FROM ee_maintenance_record
        WHERE machine_id = ? AND bgn_date = ?
    """
    params = [machine_id, date]
    if e_tag:
        query += " AND e_tag = ?"
        params.append(e_tag)
    query += " ORDER BY bgn_time"

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return f"{machine_id} ({date}) 沒有資料"

    tag_label = {"R": "修機", "S": "改機", "P": "改機", "ENG": "工程異常", "QC": "品保"}
    lines = [f"{machine_id} ({date}) 明細:"]
    for r in rows:
        label = tag_label.get(r["e_tag"], r["e_tag"])
        cause = r["cause"] or ""
        desc = r["description"] or ""
        std = get_std_hours(r["job_code"])
        dur = r["dur"] if r["dur"] is not None else 0.0
        wait_dur = r["wait_dur"] if r["wait_dur"] is not None else 0.0
        overtime_note = ""
        if std is not None and dur > std:
            overtime_note = f" [超時](實耗{dur:.2f}hr - 標準{std}hr)"
        lines.append(
            f"{r['bgn_time']} [{label}/{r['job_code']}] {cause} {desc} "
            f"耗時{dur:.2f}hr Wait{wait_dur:.2f}hr (工程師:{r['engineer_id']}){overtime_note}"
        )
    return "\n".join(lines)


def live_status_reply(machine_id: str) -> str:
    """
    回傳機台目前即時狀態。
    優先找 end_time 是空的(代表還在進行中)最新一筆；
    沒有進行中的話，回傳最近一筆已結束的紀錄摘要。
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT bgn_date, bgn_time, job_code, e_tag, cause, description, engineer_id
        FROM ee_maintenance_record
        WHERE machine_id = ? AND (end_time IS NULL OR end_time = '')
        ORDER BY bgn_date DESC, bgn_time DESC
        LIMIT 1
    """, (machine_id,))
    row = cur.fetchone()

    tag_label = {"R": "修機", "S": "改機", "P": "改機"}

    if row:
        label = tag_label.get(row["e_tag"], row["e_tag"])
        std = get_std_hours(row["job_code"])
        elapsed = None
        try:
            bgn_dt = datetime.datetime.strptime(f"{row['bgn_date']} {row['bgn_time']}", "%Y-%m-%d %H:%M")
            elapsed = (datetime.datetime.now() - bgn_dt).total_seconds() / 3600.0
        except (TypeError, ValueError):
            pass

        lines = [f"{machine_id} 目前狀態: 進行中【{label}/{row['job_code']}】"]
        lines.append(f"開始時間: {row['bgn_date']} {row['bgn_time']}")
        if row["cause"]:
            lines.append(f"原因: {row['cause']}")
        if elapsed is not None:
            over_note = ""
            if std is not None and elapsed > std:
                over_note = f" [已超時](標準{std}hr)"
            lines.append(f"已耗時: {elapsed:.2f}hr{over_note}")
        if row["engineer_id"]:
            lines.append(f"工程師: {row['engineer_id']}")
        conn.close()
        return "\n".join(lines)

    cur.execute("""
        SELECT bgn_date, bgn_time, end_time, job_code, e_tag, dur, cause
        FROM ee_maintenance_record
        WHERE machine_id = ?
        ORDER BY bgn_date DESC, bgn_time DESC
        LIMIT 1
    """, (machine_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return f"{machine_id} 查無任何修機/改機紀錄"

    label = tag_label.get(row["e_tag"], row["e_tag"])
    dur = row["dur"] if row["dur"] is not None else 0.0
    return (
        f"{machine_id} 目前狀態: 無進行中的修機/改機\n"
        f"最近一筆: {row['bgn_date']} {row['bgn_time']}~{row['end_time'] or ''} "
        f"【{label}/{row['job_code']}】耗時{dur:.2f}hr {row['cause'] or ''}"
    )


def utilization_reply(machine_id: str) -> str:
    """回傳該機台最新一筆稼動率資料(來自 utilization_record，由 cpis_utilization_scraper.py 寫入)"""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT * FROM utilization_record
            WHERE ENTITY = ?
            ORDER BY fetched_at DESC
            LIMIT 1
        """, (machine_id,))
        row = cur.fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return "稼動率資料表還不存在，請先跑一次 cpis_utilization_scraper.py"
    conn.close()

    if not row:
        return f"{machine_id} 查無稼動率資料(可能不是DA機台，或尚未抓取到這台)"

    keys = row.keys()

    def g(k):
        return row[k] if k in keys else None

    lines = [f"{machine_id} 稼動率資料(擷取時間:{g('fetched_at')})"]
    for col, cn in [("UTIL", "稼動"), ("R-EFF", "R-EFF"), ("SETUP", "改機占比"),
                     ("STOP", "停機"), ("IDLE", "閒置"), ("QTY", "數量")]:
        v = g(col)
        if v is not None:
            lines.append(f"{cn}: {v}")
    return "\n".join(lines)


def downrate_reply(machine_id: str) -> str:
    """
    回傳該機台最新一筆稼動率資料裡，UTIL/RUN以外的所有停機/閒置細項分類
    (改機、工程、SWR、PM、停機、品保、閒置、等待修機、修機中...)
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT * FROM utilization_record
            WHERE ENTITY = ?
            ORDER BY fetched_at DESC
            LIMIT 1
        """, (machine_id,))
        row = cur.fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return "稼動率資料表還不存在，請先跑一次 cpis_utilization_scraper.py"
    conn.close()

    if not row:
        return f"{machine_id} 查無稼動率資料(可能不是DA機台，或尚未抓取到這台)"

    keys = row.keys()

    def g(k):
        return row[k] if k in keys else None

    # UTIL/RUN是稼動的部分，這裡只列「非稼動」的停機/閒置細項
    downrate_cols = [
        ("W-SET", "等待改機"), ("SETUP", "改機"), ("ENG", "工程"), ("SWR", "SWR"),
        ("PM", "PM保養"), ("STOP", "停機"), ("QC", "品保"), ("IDLE", "閒置"),
        ("W-REP", "等待修機"), ("IN-REP", "修機中"),
    ]

    lines = [f"{machine_id} 停機/閒置細項(擷取時間:{g('fetched_at')})"]
    util = g("UTIL")
    if util is not None:
        lines.append(f"稼動(UTIL): {util}")
    found_any = False
    for col, cn in downrate_cols:
        v = g(col)
        if v is not None:
            lines.append(f"{cn}: {v}")
            found_any = True
    if not found_any:
        lines.append("(這批資料沒有停機細項欄位)")
    return "\n".join(lines)


# DB800/DB830/DB700 三種機型各自涵蓋的機台代號清單
MODEL_GROUPS = {
    "DB800": [f"BAA{str(i).zfill(2)}" for i in range(1, 12)],   # BAA01~BAA11
    "DB830": [f"BAB{str(i).zfill(2)}" for i in range(1, 21)],   # BAB01~BAB20
    "DB700": [f"BA7{str(i).zfill(2)}" for i in range(1, 24)],   # BA701~BA723
    "Esec2100": [f"BA{n:03d}" for n in [
   205,206,207,208,209,210,211,212,214,215,216,218,219,221,222,223,224,
   226,227,228,229,230,231,232,233,234,235,236,237,238,239,240,241,242,
   243,244,245,246,247,248,249,250,251,252,253,254,255,256,257,258,259,
   401,402,403,404,405,406,407,408,409,410,411,412,413,414,415,416,417,
   418,419,420,421,422,423,424,425,426,427,
]],

"CM700": [f"BA{n:03d}" for n in [
   802,803,804,805,806,807,808,809,811,812,813,816,817,818,819,820,821,
   822,823,824,830,831,833,836,837,839,840,842,843,844,855,856,857,862,
   863,864,865,866,867,868,869,871,872,874,875,876,877,878,880,881,882,
   883,884,885,886,887,888,889,890,891,892,893,
]],
}

# EPOXY(DB) 與 Epoxy 是CPIS官方「GROUP」彙總表裡本來就有的複合分類，
# 對照官方定義：EPOXY(DB) = DB700+DB800+DB830 加總；Epoxy = 再加上Esec2100(2100advi+2100SD)。
# 直接引用上面已經定義好的清單組合而成，不重新打一次機台代號，避免抄錯、也讓
# 之後若DB700/DB800/DB830/Esec2100清單有調整時，這兩組會自動跟著更新。
MODEL_GROUPS["EPOXY(DB)"] = MODEL_GROUPS["DB700"] + MODEL_GROUPS["DB800"] + MODEL_GROUPS["DB830"]
MODEL_GROUPS["Epoxy"] = MODEL_GROUPS["Esec2100"] + MODEL_GROUPS["EPOXY(DB)"]


def _machine_live_status_short(cur, machine_id):
    """簡化版即時狀態，只回傳簡短標記(修機中/改機中/正常)，供群組彙總用"""
    cur.execute("""
        SELECT e_tag FROM ee_maintenance_record
        WHERE machine_id = ? AND (end_time IS NULL OR end_time = '')
        ORDER BY bgn_date DESC, bgn_time DESC LIMIT 1
    """, (machine_id,))
    row = cur.fetchone()
    if row:
        tag_label = {"R": "修機中", "S": "改機中", "P": "改機中"}
        return tag_label.get(row["e_tag"], f"{row['e_tag']}中")
    return "正常"


def _to_float_percent(s):
    """把 '54.5 %' 這種字串轉成浮點數 54.5，轉不了回傳None"""
    try:
        return float(str(s).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# downrate各細項欄位，跟downrate_reply()裡列的一致
_DOWNRATE_COLS = [
    ("W-SET", "等待改機"), ("SETUP", "改機"), ("ENG", "工程"), ("SWR", "SWR"),
    ("PM", "PM保養"), ("STOP", "停機"), ("QC", "品保"), ("IDLE", "閒置"),
    ("W-REP", "等待修機"), ("IN-REP", "修機中"),
]


def _group_downrate_avg(cur, machine_ids):
    """
    對群組內每台機台，抓最新一筆utilization_record，把UTIL跟各downrate細項平均起來。
    回傳 (avg_dict, n_found)；n_found是這個群組裡有稼動率資料的機台數，0代表完全沒資料。
    """
    util_vals = []
    col_vals = {c: [] for c, _ in _DOWNRATE_COLS}
    n_found = 0

    for mid in machine_ids:
        cur.execute("""
            SELECT * FROM utilization_record WHERE ENTITY = ?
            ORDER BY fetched_at DESC LIMIT 1
        """, (mid,))
        row = cur.fetchone()
        if not row:
            continue
        n_found += 1
        keys = row.keys()

        if "UTIL" in keys:
            v = _to_float_percent(row["UTIL"])
            if v is not None:
                util_vals.append(v)

        for col, _ in _DOWNRATE_COLS:
            if col in keys:
                v = _to_float_percent(row[col])
                if v is not None:
                    col_vals[col].append(v)

    if n_found == 0:
        return None, 0

    avg = {}
    if util_vals:
        avg["UTIL"] = sum(util_vals) / len(util_vals)
    for col, _ in _DOWNRATE_COLS:
        vals = col_vals[col]
        if vals:
            avg[col] = sum(vals) / len(vals)

    return avg, n_found


def db_group_reply(group_names=None) -> str:
    """
    回傳指定機型群組的分組統計：
    每組列出目前修機中/改機中/正常的統計人數、每台機台的簡短狀態清單，
    以及該組downrate(改機/工程/停機/閒置...)平均百分比。
    排版參考同事機器人的風格：用「·」分隔數字、緊湊單行呈現。

    group_names: 要顯示哪幾組(對應MODEL_GROUPS的key清單)。
                 不指定時維持原本行為，只顯示DB800/DB830/DB700三組，
                 不會因為MODEL_GROUPS裡新增了其他機型群組(如Esec2100/CM700)
                 就自動全部混在一起顯示。
    """
    conn = get_conn()
    cur = conn.cursor()

    if group_names is None:
        group_names = ["DB800", "DB830", "DB700"]

    now_str = datetime.datetime.now().strftime("%m/%d %H:%M")
    lines = [f"【{'/'.join(group_names)}機型群組】{now_str}"]

    for group_name in group_names:
        machine_ids = MODEL_GROUPS.get(group_name)
        if not machine_ids:
            lines.append("")
            lines.append(f"▶{group_name}  (查無此機型群組定義)")
            continue
        statuses = {mid: _machine_live_status_short(cur, mid) for mid in machine_ids}

        repair_cnt = sum(1 for s in statuses.values() if s == "修機中")
        setup_cnt = sum(1 for s in statuses.values() if s == "改機中")
        normal_cnt = sum(1 for s in statuses.values() if s == "正常")

        lines.append("")
        lines.append(f"▶{group_name}  共{len(machine_ids)}台 · 修機{repair_cnt} · 改機{setup_cnt} · 正常{normal_cnt}")

        avg, n_found = _group_downrate_avg(cur, machine_ids)
        if avg is None:
            lines.append(f"  downrate: 無資料")
        else:
            dr_parts = []
            if "UTIL" in avg:
                dr_parts.append(f"稼動{avg['UTIL']:.1f}%")
            for col, cn in _DOWNRATE_COLS:
                if col in avg:
                    dr_parts.append(f"{cn}{avg[col]:.1f}%")
            lines.append(f"  downrate(有資料{n_found}/{len(machine_ids)}台): " + " · ".join(dr_parts))

        # 只列有狀況(修機中/改機中)的機台，正常的不逐台列出，避免洗版
        abnormal = [f"{mid}:{s}" for mid, s in statuses.items() if s != "正常"]
        if abnormal:
            lines.append(f"  異常機台: " + "、".join(abnormal))

    conn.close()
    return "\n".join(lines)


# CPIS Utilization Analysis 頁面最下方「GROUP」彙總表裡，官方本身就有的群組名稱清單
# (這些是cpis_utilization_scraper.py抓取時，連同機台明細一起原封不動存進DB的整列彙總數字，
# 跟db_group_reply()裡自己逐台平均算出來的數字是兩回事，可能因為官方是用產量加權平均
# 等不同算法而有些微落差，這裡查的是官方原始列)
OFFICIAL_GROUP_LABELS = [
    "2100SD", "DATACON8800", "DB700", "DB800", "DB830",
    "EPOXY(DB)", "Epoxy", "Flip Chip", "LOC",
]


def group_official_downrate_reply(group_label: str) -> str:
    """
    回傳CPIS Utilization Analysis頁面最下方「GROUP」彙總表裡，
    指定群組(group_label需完全對應OFFICIAL_GROUP_LABELS裡的名稱，含大小寫)
    的官方原始一列數字(UTIL跟各downrate細項)，不是我們自己逐台平均算出來的。

    這批資料存在utilization_record裡，跟一般個別機台的資料共用同一張表，
    差別在於：這種GROUP彙總列沒有ENTITY欄位(該欄位是NULL)，
    個別機台列一定有ENTITY(機台代號)，用這點區分兩者。
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT * FROM utilization_record
            WHERE MODEL = ? AND (ENTITY IS NULL OR ENTITY = '')
            ORDER BY fetched_at DESC
            LIMIT 1
        """, (group_label,))
        row = cur.fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return "稼動率資料表還不存在，請先跑一次 cpis_utilization_scraper.py"
    conn.close()

    if not row:
        return f"{group_label} 查無官方GROUP彙總資料(可能尚未抓取到，或這次抓取時CPIS頁面上沒有這個群組)"

    keys = row.keys()

    def g(k):
        return row[k] if k in keys else None

    lines = [f"{group_label}(CPIS官方GROUP彙總) 擷取時間:{g('fetched_at')}"]
    util = g("UTIL")
    if util is not None:
        lines.append(f"稼動(UTIL): {util}")
    for col, cn in _DOWNRATE_COLS:
        v = g(col)
        if v is not None:
            lines.append(f"{cn}: {v}")
    run = g("RUN")
    if run is not None:
        lines.append(f"RUN: {run}")
    return "\n".join(lines)


def health_reply(machine_id: str) -> str:
    """回傳該機台最新一筆設備健康監控資料(來自 health_monitor_record，由 health_dashboard_scraper.py 寫入)"""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT * FROM health_monitor_record
            WHERE machine_id = ?
            ORDER BY fetched_at DESC
            LIMIT 1
        """, (machine_id,))
        row = cur.fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return "設備健康監控資料表還不存在，請先跑一次 health_dashboard_scraper.py"
    conn.close()

    if not row:
        return f"{machine_id} 查無設備健康監控資料(可能沒在異常清單裡，或尚未抓取)"

    keys = row.keys()

    def g(k):
        return row[k] if k in keys else None

    lines = [f"{machine_id}({g('model')}) 健康分:{g('health_score')} (擷取時間:{g('fetched_at')})"]
    if g("live_status"):
        lines.append(f"即時狀態: {g('live_status')}")
    if g("duration"):
        lines.append(f"已持續: {g('duration')}")
    if g("eta"):
        lines.append(f"預計完成: {g('eta')}")
    if g("overdue"):
        lines.append(f"逾期: {g('overdue')}")
    if g("wip"):
        lines.append(f"WIP: {g('wip')}")
    if g("jcode"):
        lines.append(f"JCODE: {g('jcode')}")
    if g("handler"):
        lines.append(f"處理人: {g('handler')}")
    return "\n".join(lines)


def full_info_reply(machine_id: str) -> str:
    """
    回傳機台的完整資訊：即時狀態 + 修機/改機統計摘要 + 最新稼動率 + 設備健康監控
    (有資料才附上)。單純打機台代號、沒加其他關鍵字時用這個，取代原本只回即時狀態。
    """
    parts = [
        live_status_reply(machine_id),
        "",
        summary_reply(machine_id),
        "",
        utilization_reply(machine_id),
    ]

    health = health_reply(machine_id)
    if "查無設備健康監控資料" not in health and "資料表還不存在" not in health:
        parts.append("")
        parts.append(health)

    return "\n".join(parts)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1].upper() == "DB":
        print(db_group_reply())
        sys.exit(0)

    if len(sys.argv) >= 2 and sys.argv[1] in MODEL_GROUPS and sys.argv[1] not in ("DB800", "DB830", "DB700"):
        # 讓 CM700 / Esec2100 這類額外群組也能用 python query_bot.py <群組名> 直接測試
        print(db_group_reply([sys.argv[1]]))
        sys.exit(0)

    if len(sys.argv) >= 3 and sys.argv[2] == "official":
        # 查CPIS官方GROUP彙總表的原始列，例如: python query_bot.py "DB800" official
        # 用sys.argv[1]原始大小寫(不轉大寫)，因為Epoxy/EPOXY(DB)/Flip Chip等
        # 官方名稱本身大小寫不一致，轉大寫會對不到資料庫裡存的原始文字
        print(group_official_downrate_reply(sys.argv[1]))
        sys.exit(0)

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    machine = sys.argv[1].upper()
    mode = sys.argv[2] if len(sys.argv) > 2 else "summary"

    if mode == "detail":
        print(detail_reply(machine))
    elif mode == "live":
        print(live_status_reply(machine))
    elif mode == "util":
        print(utilization_reply(machine))
    elif mode == "downrate":
        print(downrate_reply(machine))
    elif mode == "health":
        print(health_reply(machine))
    elif mode == "full":
        print(full_info_reply(machine))
    else:
        print(summary_reply(machine))
