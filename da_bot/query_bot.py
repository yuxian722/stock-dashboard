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

# Windows主控台預設用cp950(繁體中文)編碼，回覆內容可能含emoji，print()會
# 直接丟UnicodeEncodeError把腳本弄當掉。改成把stdout/stderr強制用utf-8
# 輸出，encode不了的字元用errors="replace"跳過。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 「<群組>改機」「工時」查詢要沿用hourly_push.py裡已經對過、修正過好幾次的
# 機台代號→機型群組對照(_group_for_machine)、依群組區分的真正改機job_code
# 判斷(_changeover_jcode_category)、跟班別對齊的「今日」定義
# (_shift_day_bounds)，不要在這裡自己重新猜一份、重蹈之前LOC/CM700分類
# 搞錯的覆轍。這幾個都是
# 不碰資料庫的純函式，直接import沿用沒有交互汙染DB_PATH的疑慮。
import hourly_push
import engineer_master

DB_PATH = "da_maintenance.db"

# 各 JOB CODE 的標準工時(小時)，用來判斷是否超時
# key 是完整 JOB CODE 或前綴(完整對不到就用前綴比對)
JOB_CODE_STD_HOURS = {
    "CED-M2": 2.9,  # Multi step(2 dies)，2026/08/09使用者提供
    "CED-M3": 2.9,  # Multi step(3 dies)，2026/08/09使用者提供
    "CED-M4": 2.9,  # Multi step(4 dies)，2026/08/09使用者提供
    "CED": 2.3,     # 頂針(CED-1等)，依經驗值，2026/07/17確認
    "CE": 3.0,      # 改機標準工時(依經驗值，2026/07/17確認)
    "CEE": 3.0,     # 設備工程 CEE 類改機標準工時
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


# CPIS PM/REPAIR/SETUP Monitor頁面的STATUS代碼(cpis_pm_monitor_scraper.py
# 抓的即時機況)，跟畫面上的顏色圖例對照：黃=修機中、藍=保養中、淺綠=改機中、
# 綠=等待修機、白/灰=等待改機
_PM_STATUS_ZH = {
    "IN-REPAIR": "修機中", "WAIT-REPAIR": "等待修機",
    "SETUP": "改機中", "WAIT-SETUP": "等待改機",
    "PM": "保養中", "ENG": "工程異常",
}


def get_latest_pm_monitor_status(cur, machine_id):
    """
    查pm_monitor_record最新一批快照裡，該機台的即時狀態列。查不到(機台
    目前不在PM/REPAIR/SETUP Monitor的異常清單裡，或這個資料表還沒抓過)
    回傳None，呼叫端要自己決定要不要退回用EE Maintenance歷史紀錄推論。
    """
    try:
        cur.execute("SELECT MAX(fetched_at) FROM pm_monitor_record")
    except sqlite3.OperationalError:
        return None  # 資料表還不存在(還沒跑過cpis_pm_monitor_scraper.py)
    row = cur.fetchone()
    latest = row[0] if row else None
    if not latest:
        return None
    cur.execute("""
        SELECT status, jcode, operator, in_time, outplan, lot_no, model
        FROM pm_monitor_record
        WHERE fetched_at = ? AND entity = ?
    """, (latest, machine_id))
    return cur.fetchone()


def _elapsed_hours_since(time_str):
    """PM Monitor的IN TIME是"2026/08/09 17:29"這種格式，算到現在經過幾小時。"""
    try:
        dt = datetime.datetime.strptime(time_str, "%Y/%m/%d %H:%M")
    except (TypeError, ValueError):
        return None
    return (datetime.datetime.now() - dt).total_seconds() / 3600.0


def live_status_reply(machine_id: str) -> str:
    """
    回傳機台目前即時狀態。優先用PM/REPAIR/SETUP Monitor的即時快照(真的是
    當下的機況，不是推論的)；查不到該機台(不在異常清單裡，代表正常運作中，
    或這個資料表還沒抓過)才退回用EE Maintenance歷史紀錄推論——找end_time
    是空的(代表還在進行中)最新一筆，沒有進行中的話回傳最近一筆已結束的
    紀錄摘要。
    """
    conn = get_conn()
    cur = conn.cursor()

    pm_row = get_latest_pm_monitor_status(cur, machine_id)
    if pm_row is not None:
        status_zh = _PM_STATUS_ZH.get(pm_row["status"], pm_row["status"])
        lines = [f"{machine_id} 目前狀態(即時): {status_zh}"]
        if pm_row["jcode"]:
            lines.append(f"代碼: {pm_row['jcode']}")
        elapsed = _elapsed_hours_since(pm_row["in_time"])
        if elapsed is not None:
            std = get_std_hours(pm_row["jcode"])
            over_note = f" [已超時](標準{std}hr)" if (std is not None and elapsed > std) else ""
            lines.append(f"已耗時: {elapsed:.2f}hr{over_note}")
        if pm_row["operator"]:
            lines.append(f"人員: {pm_row['operator']}")
        if pm_row["lot_no"]:
            lines.append(f"批號: {pm_row['lot_no']}")
        conn.close()
        return "\n".join(lines)

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

# DB700/DB800/DB830的MODEL名稱跟utilization_record.MODEL欄位的值完全對應，
# 可以直接查CPIS官方回報的真實機台清單，不用像Esec2100/CM700那樣手動維護
# 固定範圍的代號清單——手動清單容易跟實際機台增減不同步(2026/08/09使用者
# 就抓到DB830這樣手動清單多算了1台，寫54台但CPIS Utilization Analysis頁面
# 實際只有53台)
_DYNAMIC_MODEL_GROUPS = ("DB700", "DB800", "DB830")


def _group_machine_ids(cur, group_name):
    """
    取得群組實際機台清單。DB700/DB800/DB830直接查utilization_record最新一批
    資料裡MODEL=group_name的真實ENTITY清單；EPOXY(DB)/Epoxy這種複合群組是
    底下子群組清單加總；其餘群組(Esec2100/CM700)沒有可以1:1對應的MODEL欄位值，
    還是用MODEL_GROUPS裡手動維護的代號清單。utilization_record還沒抓過資料時，
    退回用MODEL_GROUPS的清單當備援，不會讓查詢整個失敗。
    """
    if group_name == "EPOXY(DB)":
        result = []
        for base in ("DB700", "DB800", "DB830"):
            result.extend(_group_machine_ids(cur, base))
        return result
    if group_name == "Epoxy":
        result = list(MODEL_GROUPS["Esec2100"])
        result.extend(_group_machine_ids(cur, "EPOXY(DB)"))
        return result

    if group_name not in _DYNAMIC_MODEL_GROUPS:
        return MODEL_GROUPS.get(group_name, [])

    cur.execute("SELECT MAX(fetched_at) FROM utilization_record")
    row = cur.fetchone()
    latest = row[0] if row else None
    if not latest:
        return MODEL_GROUPS.get(group_name, [])

    cur.execute("""
        SELECT DISTINCT ENTITY FROM utilization_record
        WHERE fetched_at = ? AND MODEL = ? AND ENTITY IS NOT NULL AND ENTITY != ''
    """, (latest, group_name))
    ids = [r["ENTITY"] for r in cur.fetchall()]
    return ids or MODEL_GROUPS.get(group_name, [])


def _pm_status_map(cur):
    """
    回傳目前PM/REPAIR/SETUP Monitor快照的{機台代號: STATUS}對照表，以及
    有沒有抓過PM Monitor資料的旗標(has_data)。

    has_data=False代表cpis_pm_monitor_scraper.py還沒跑過(資料表不存在或
    完全沒資料)，這種情況呼叫端要整批退回用EE Maintenance歷史紀錄推論；
    has_data=True則代表這是當下真正完整的異常機台清單，不在對照表裡的機台
    就是正常，不用再退回EE Maintenance——不然機台已經在PM Monitor上恢復
    正常後，還可能被EE Maintenance裡沒關閉的舊紀錄誤判成仍在修機/改機
    (這正是2026/08/09使用者回報「修機0‧改機0」不準的根因，之前是逐台各自
    查PM Monitor有沒有這台，查不到就當「這個資料表還沒抓過」退回EE Maintenance，
    沒辦法區分「這台真的正常」跟「PM Monitor整批都沒資料」)。
    """
    rows, has_data = _pm_latest_rows(cur)
    return {r["entity"]: r["status"] for r in rows}, has_data


def _pm_latest_rows(cur):
    """
    回傳pm_monitor_record最新一批快照的原始列(entity/status/jcode/operator/
    in_time)，以及有沒有抓過PM Monitor資料的旗標(has_data)。db_group_reply()
    的「修機超時機台」清單要用到in_time/operator才能算出修機多久、是誰在修，
    _pm_status_map()只回傳status不夠用，兩者共用這支底層查詢。
    """
    try:
        cur.execute("SELECT MAX(fetched_at) FROM pm_monitor_record")
    except sqlite3.OperationalError:
        return [], False
    row = cur.fetchone()
    latest = row[0] if row else None
    if not latest:
        return [], False
    cur.execute("""
        SELECT entity, status, jcode, operator, in_time
        FROM pm_monitor_record WHERE fetched_at = ?
    """, (latest,))
    return cur.fetchall(), True


def _machine_live_status_short(cur, machine_id, pm_status_map=None, pm_has_data=False):
    """
    簡化版即時狀態，只回傳簡短標記(修機中/改機中/正常)，供群組彙總用。
    pm_has_data=True時優先用_pm_status_map()查到的PM Monitor即時快照(是當下
    真正的機況，不是推論的)；pm_has_data=False(PM Monitor整批都還沒抓過資料)
    才退回用EE Maintenance歷史紀錄推論，維持原本行為。
    """
    if pm_has_data:
        status = (pm_status_map or {}).get(machine_id)
        if status is None:
            return "正常"
        return _PM_STATUS_ZH.get(status, status)

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

    pm_rows, pm_has_data = _pm_latest_rows(cur)
    pm_status_map = {r["entity"]: r["status"] for r in pm_rows}
    pm_row_by_entity = {r["entity"]: r for r in pm_rows}
    now = datetime.datetime.now()

    for group_name in group_names:
        machine_ids = _group_machine_ids(cur, group_name)
        if not machine_ids:
            lines.append("")
            lines.append(f"▶{group_name}  (查無此機型群組定義)")
            continue
        statuses = {
            mid: _machine_live_status_short(cur, mid, pm_status_map, pm_has_data)
            for mid in machine_ids
        }

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

        # 異常機台清單：只列「修機中且已經超過1小時」的機台(2026/08/10使用者
        # 要求)，不再像之前那樣把改機中/工程異常/等待修機全部混在一起列，
        # 避免洗版又抓不到真正該關注的重點(修機拖太久的機台)。
        # 沒抓過PM Monitor資料(pm_has_data=False)時沒有in_time可以算修機
        # 多久，這裡就不列，跟其他仰賴PM Monitor即時快照的功能一致。
        overtime_repairs = []
        if pm_has_data:
            for mid in machine_ids:
                row = pm_row_by_entity.get(mid)
                if row is None or row["status"] != "IN-REPAIR":
                    continue
                elapsed = hourly_push._pm_elapsed_hours(row["in_time"], now)
                if elapsed is None or elapsed <= 1.0:
                    continue
                operator = engineer_master.format_engineer(row["operator"]) if row["operator"] else "?"
                overtime_repairs.append(f"{mid} 修機超時{elapsed:.2f}hr/{operator}")
        if overtime_repairs:
            lines.append(f"  異常機台(修機超時1hr以上): " + "、".join(overtime_repairs))

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


# 「<群組>改機」查詢：呼叫端(teamplus_listener.py)要直接傳內部代號進來，
# 內部代號要跟hourly_push._group_for_machine()回傳的值完全一致(ESEC/DB/
# LOC/FC)，"EPOXY"是ESEC+DB合併(跟hourly_push.py的EPOXY定義一致)。注意
# _group_for_machine()對FlipChip機台回傳的是"FC"不是"FlipChip"，呼叫端
# 傳"FlipChip"字面近來會永遠比對不到、查詢結果是空的。
# 內部代號→顯示用名稱，只用在回覆文字的標題。
_CHANGEOVER_GROUP_DISPLAY = {
    "EPOXY": "EPOXY", "ESEC": "ESEC", "DB": "DB", "LOC": "LOC", "FC": "FlipChip",
}


def _changeover_rows_for_group(cur, group_name, now):
    """
    回傳指定機型群組今日(跟班別對齊，見hourly_push._shift_day_bounds())已
    完成的真正改機(job_code屬於CED/CEE/CD類別)紀錄原始列(machine_id/
    job_code/engineer_id/dur)，group_name="EPOXY"時涵蓋ESEC+DB。
    """
    shift_date, next_date = hourly_push._shift_day_bounds(now)
    cur.execute("""
        SELECT DISTINCT machine_id, bgn_date, bgn_time, end_time, job_code, engineer_id, dur
        FROM ee_maintenance_record
        WHERE e_tag = 'S' AND (
            (end_date = ? AND end_time >= ?)
            OR (end_date = ? AND end_time < ?)
        )
    """, (shift_date, hourly_push.SHIFT_CHANGE_TIME, next_date, hourly_push.SHIFT_CHANGE_TIME))

    rows = []
    for r in cur.fetchall():
        g = hourly_push._group_for_machine(r["machine_id"])
        if group_name == "EPOXY":
            if g not in ("ESEC", "DB"):
                continue
        elif g != group_name:
            continue
        # 用這一列真正對應到的機型群組(g)去判斷job_code算不算改機，不是用
        # 查詢參數group_name——group_name="EPOXY"時混合了ESEC/DB兩組，各自
        # 要用各自的job_code標準比對(雖然目前ESEC/DB剛好共用同一套標準，
        # 但這樣寫才不會在未來兩者標準分家時算錯)
        category = hourly_push._changeover_jcode_category(g, r["job_code"])
        if category is None:
            continue
        rows.append({
            "machine_id": r["machine_id"], "job_code": r["job_code"],
            "engineer_id": r["engineer_id"], "dur": r["dur"], "category": category,
            "end_time": r["end_time"],
        })
    return rows


# 各群組job_code分類標籤的固定顯示順序，沒有資料的類別不顯示。
# ESEC/DB(EPOXY)用CED機台/CEE機台/CD機台，LOC用CN機台/CD機台，兩邊「CD機台」
# 剛好同名，用同一個順序清單涵蓋全部類別即可，不用分群組各自維護一份。
_CHANGEOVER_LABEL_ORDER = ["CED機台", "CEE機台", "CD機台", "CN機台"]


def _category_avg_parts(rows):
    """把rows(_changeover_rows_for_group()回傳、已經帶好category欄位的)依
    job_code分類，回傳["CED機台3台平均1.2hr", ...]這種字串清單(照
    _CHANGEOVER_LABEL_ORDER固定順序，沒有資料的類別不顯示)。"""
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r["dur"] or 0.0)
    parts = []
    for label in _CHANGEOVER_LABEL_ORDER:
        durs = by_cat.get(label)
        if not durs:
            continue
        parts.append(f"{label}{len(durs)}台平均{sum(durs) / len(durs):.1f}hr")
    return parts


def group_changeover_detail_reply(group_name: str, now: datetime.datetime = None) -> str:
    """
    「<群組>改機」查詢(例如"DB改機")：今日該機型群組已完成的真正改機明細，
    包含總台數、依CED/CEE/CD分類的平均改機工時，以及依人員(工號)分類的
    改機台數+各分類平均工時(2026/08/09使用者要求)。「今日」跟班別對齊。
    group_name要用內部代號(ESEC/DB/LOC/FC/EPOXY)，顯示文字會轉成
    _CHANGEOVER_GROUP_DISPLAY對應的名稱(FC顯示成FlipChip)。
    """
    if now is None:
        now = datetime.datetime.now()
    display_name = _CHANGEOVER_GROUP_DISPLAY.get(group_name, group_name)
    conn = get_conn()
    cur = conn.cursor()
    rows = _changeover_rows_for_group(cur, group_name, now)
    conn.close()

    if not rows:
        return f"{display_name}改機 今日目前沒有完成的改機紀錄"

    lines = [f"【{display_name}改機】今日共{len(rows)}台"]

    # 早班(07:30~19:30)/夜班(19:30~次日07:30)改機台數(2026/08/10使用者要求)，
    # 跟hourly_push.get_epoxy_done_by_shift()同一套依end_time判斷班別的邏輯。
    night_count = sum(1 for r in rows if hourly_push._is_night_shift(r["end_time"]))
    day_count = len(rows) - night_count
    shift_parts = [f"{label}{n}台" for label, n in (("早班", day_count), ("夜班", night_count)) if n]
    if shift_parts:
        lines.append(" ".join(shift_parts))

    # MFG(產線技術員)/EE(設備工程師)改機台數(2026/08/10使用者要求)，資料來源
    # 是engineer_master.py的工號→部門對照(使用者提供的DA EE Maintenance
    # Record人員名冊)。
    dept_counts = engineer_master.dept_breakdown([r["engineer_id"] for r in rows])
    dept_parts = [
        f"{label}{dept_counts[label]}台" for label in ("MFG", "EE", "未知") if dept_counts[label]
    ]
    if dept_parts:
        lines.append(" ".join(dept_parts))

    cat_parts = _category_avg_parts(rows)
    if cat_parts:
        lines.append(" ".join(cat_parts))

    by_engineer = {}
    for r in rows:
        eng = r["engineer_id"] or "未指定"
        by_engineer.setdefault(eng, []).append(r)

    lines.append("")
    lines.append("人員明細:")
    for eng, eng_rows in sorted(by_engineer.items(), key=lambda kv: -len(kv[1])):
        eng_cat_parts = _category_avg_parts(eng_rows)
        lines.append(f"{engineer_master.format_engineer(eng)}  改機{len(eng_rows)}台  " + " ".join(eng_cat_parts))

    return "\n".join(lines)


def _workhours_rows(cur, now, engineer_id=None):
    """
    回傳今日(跟班別對齊)所有e_tag屬於R(修機)/S(改機)的紀錄(e_tag/engineer_id/
    dur)。engineer_id有指定時只查該工號，不指定時回傳全部工號的紀錄。
    e_tag='S'的紀錄要另外依機台所屬群組篩掉不是該群組真正改機類別的
    job_code(INK補墨水/AING視覺校正這類生產中小動作)，跟這個檔案其他
    「改機」統計的認定標準一致(e_tag='R'的修機紀錄不用篩，全部都算修機
    工時)。
    """
    shift_date, next_date = hourly_push._shift_day_bounds(now)
    params = [shift_date, hourly_push.SHIFT_CHANGE_TIME, next_date, hourly_push.SHIFT_CHANGE_TIME]
    sql = """
        SELECT DISTINCT machine_id, bgn_date, bgn_time, e_tag, job_code, engineer_id, dur
        FROM ee_maintenance_record
        WHERE e_tag IN ('R', 'S') AND (
            (end_date = ? AND end_time >= ?)
            OR (end_date = ? AND end_time < ?)
        )
    """
    if engineer_id:
        sql += " AND engineer_id = ?"
        params.append(engineer_id)
    cur.execute(sql, params)

    rows = []
    for r in cur.fetchall():
        if r["e_tag"] == "S":
            g = hourly_push._group_for_machine(r["machine_id"])
            if hourly_push._changeover_jcode_category(g, r["job_code"]) is None:
                continue
        rows.append(r)
    return rows


def workhours_reply(engineer_id: str = None, now: datetime.datetime = None) -> str:
    """
    「工時」查詢：今日各工號人員的修機+改機總工時(2026/08/09使用者要求)。
    engineer_id指定時只顯示該工號；不指定時列出今日所有有紀錄的工號，
    依總工時由多到少排序。「今日」跟班別對齊。
    """
    if now is None:
        now = datetime.datetime.now()
    conn = get_conn()
    cur = conn.cursor()
    rows = _workhours_rows(cur, now, engineer_id)
    conn.close()

    if not rows:
        target = f"{engineer_id} " if engineer_id else ""
        return f"{target}今日目前沒有修機/改機紀錄"

    by_engineer = {}
    for r in rows:
        eng = r["engineer_id"] or "未指定"
        hrs = by_engineer.setdefault(eng, {"R": 0.0, "S": 0.0})
        hrs[r["e_tag"]] += r["dur"] or 0.0

    lines = ["【工時】今日修機+改機總工時"]
    for eng, hrs in sorted(by_engineer.items(), key=lambda kv: -(kv[1]["R"] + kv[1]["S"])):
        total = hrs["R"] + hrs["S"]
        lines.append(
            f"{engineer_master.format_engineer(eng)}  修機{hrs['R']:.1f}hr + 改機{hrs['S']:.1f}hr = 總{total:.1f}hr"
        )
    return "\n".join(lines)


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
