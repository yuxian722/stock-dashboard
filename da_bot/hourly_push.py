"""
DA 整點推播 - 訊息產生器 (過渡版，先印出文字供手動複製貼上)
用法: python3 hourly_push.py

之後要接上 team+ API 時，只需要把 main() 最後的 print(msg)
換成呼叫 API 的函式（例如 send_to_teamplus(msg)），
其他組訊息的邏輯都不用改。
"""
import sys

# Windows主控台預設用cp950(繁體中文)編碼，推播訊息裡的emoji(🔧⏳⚡等)沒辦法
# 用cp950編碼，print()會直接丟UnicodeEncodeError把腳本弄當掉。改成把stdout/
# stderr強制用utf-8輸出，encode不了的字元用errors="replace"跳過。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import sqlite3
import datetime

DB_PATH = "da_maintenance.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# 機台代號→機型群組(ESEC/DB/LOC/FC)，依代號前3碼分類，對齊同事Dashboard的
# getEntityGroup規則(APG_TeamplusBot/teamplus_bot.py的_entity_group())。
# 2026/08/09使用者確認：LOC就是CM700機型的官方GROUP分類名稱(CPIS Utilization
# Analysis頁面GROUP彙總表裡就是叫"LOC"，不是另外獨立的機型)，BA8開頭全部
# 都算LOC，不用再拆成CM700跟LOC兩組(query_bot.MODEL_GROUPS["CM700"]那份
# 清單是給查詢功能用"CM700"關鍵字查詢時用的，跟這裡的分組彙總是兩回事，
# 不影響這裡的判斷)。
_ENTITY_GROUP_PREFIXES = {
    "BA2": "ESEC", "BA4": "ESEC",
    "BA7": "DB", "BAA": "DB", "BAB": "DB",
    "BA8": "LOC",
    "BA5": "FC", "FC5": "FC", "BAD": "FC", "FC1": "FC",
}


def _group_for_machine(machine_id):
    p3 = (machine_id or "").upper()[:3]
    return _ENTITY_GROUP_PREFIXES.get(p3)
    return _ENTITY_GROUP_PREFIXES.get(p3)


# 「今日改機」認定為真正改機的job_code只有CED/CEE/CD三類(2026/08/09使用者
# 提供+確認)，也用來當「這筆e_tag='S'紀錄算不算改機」的篩選標準——CPIS的
# e_tag='S'不是每一筆都是機型改機，INK(補墨水)/AING(AI視覺校正)/CWT(換料)/
# OC(操作員備註)這類生產中的小動作也會被標成'S'，對不到這三類前綴的就不算
# 改機(不管是哪個機型群組)
_EPOXY_JCODE_CATEGORIES = [("CED", "CED機台"), ("CEE", "CEE機台"), ("CD", "CD機台")]


def _epoxy_jcode_category(job_code):
    jc = (job_code or "").upper()
    for prefix, label in _EPOXY_JCODE_CATEGORIES:
        if jc.startswith(prefix):
            return label
    return None


def get_epoxy_done_by_jcode():
    """
    EPOXY(ESEC+DB)今日已完成的改機次數，依「實際job_code」逐一列出台數
    (2026/08/09使用者要求要看到CED/CEDO/CD這些實際代碼各自的台數，方便
    肉眼核對加總對不對，不要收斂成CED/CEE/CD三個大類、把CEDO藏在CED機台
    裡看不到)。CPIS的e_tag='S'不是每一筆都是真正的機型改機——生產過程中
    很多小動作(補墨水INK、AI視覺校正AING、換料CWT、操作員備註OC...)也會
    被標成'S'，2026/08/09實測診斷發現這類小動作占了e_tag='S'紀錄的大多數，
    導致改機統計數字暴增到不合理(EPOXY改機274/315這種)。這裡只算job_code
    對得到CED/CEE/CD前綴(真正改機類別)的紀錄，對不到的一律不算改機
    (2026/08/09使用者確認)，所以回傳結果加總起來會等於get_setup_group_stats()
    裡EPOXY(ESEC+DB)的done數字。用SELECT DISTINCT防重複——同一筆真實紀錄
    理論上不該重複，這裡用DISTINCT保險。回傳{實際job_code: n}，沒有資料的
    代碼不會出現在結果裡。
    """
    today = datetime.date.today().isoformat()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT machine_id, bgn_date, bgn_time, job_code
        FROM ee_maintenance_record
        WHERE e_tag = 'S' AND end_date = ?
    """, (today,))
    rows = cur.fetchall()
    conn.close()

    result = {}
    for r in rows:
        g = _group_for_machine(r["machine_id"])
        if g not in ("ESEC", "DB"):
            continue
        if _epoxy_jcode_category(r["job_code"]) is None:
            continue
        jc = (r["job_code"] or "").upper()
        result[jc] = result.get(jc, 0) + 1
    return result


# PM/REPAIR/SETUP Monitor頁面的STATUS代碼(cpis_pm_monitor_scraper.py抓的
# 即時機況)顯示用中文名稱，跟query_bot.py的_PM_STATUS_ZH對照一致
_PM_STATUS_LABELS = [
    ("IN-REPAIR", "修機中"), ("WAIT-REPAIR", "等待修機"),
    ("SETUP", "改機中"), ("WAIT-SETUP", "等待改機"),
    ("PM", "保養中"), ("ENG", "工程異常"),
]

# PM Monitor JCODE的標準工時對照表(2026/08/09使用者提供)，跟query_bot.py/
# 原本hourly_push.py的JOB_CODE_STD_HOURS(EE Maintenance用)是不同的體系，
# 不要混用——這裡的key是PM/REPAIR/SETUP Monitor頁面JCODE欄位實際出現的代碼
PM_JCODE_STD_HOURS = {
    "CED-1": 2.3, "CED": 2.3, "CEDO": 2.3,
    "CEE": 4.17,
    "CN": 3.38,
    "CD": 0.5, "CES": 0.5, "BMP": 0.5,
}


def get_pm_jcode_std_hours(jcode):
    if not jcode:
        return None
    jc = jcode.upper()
    if jc in PM_JCODE_STD_HOURS:
        return PM_JCODE_STD_HOURS[jc]
    for prefix, std in PM_JCODE_STD_HOURS.items():
        if jc.startswith(prefix):
            return std
    return None


def get_pm_monitor_records():
    """
    回傳pm_monitor_record最新一批快照(cpis_pm_monitor_scraper.py抓的
    PM/REPAIR/SETUP Monitor即時機況)的所有列(entity/model/status/jcode/
    operator/in_time)。這個資料表用Selenium無頭瀏覽器抓，比較容易受環境
    影響、可能還沒抓過，資料表不存在或是空的時候回傳[](呼叫端要優雅跳過，
    不能讓整個推播訊息掛掉)。
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT MAX(fetched_at) FROM pm_monitor_record")
    except sqlite3.OperationalError:
        conn.close()
        return []
    row = cur.fetchone()
    latest = row[0] if row else None
    if not latest:
        conn.close()
        return []

    cur.execute("""
        SELECT entity, model, status, jcode, operator, in_time
        FROM pm_monitor_record WHERE fetched_at = ?
    """, (latest,))
    rows = cur.fetchall()
    conn.close()
    return rows


def _pm_group_stats_from_rows(rows):
    """依機型群組(ESEC/DB/LOC/FC)統計PM Monitor各STATUS台數，回傳{group: {status_code: 台數}}。"""
    stats = {g: {} for g in ("ESEC", "DB", "LOC", "FC")}
    for r in rows:
        g = _group_for_machine(r["entity"])
        if g is None:
            continue
        stats[g][r["status"]] = stats[g].get(r["status"], 0) + 1
    return stats


def get_pm_monitor_group_stats():
    """get_pm_monitor_records()的分組統計版，是CPIS當下真正的異常機況清單，
    不是像EE Maintenance歷史紀錄那樣推算的近似值。"""
    return _pm_group_stats_from_rows(get_pm_monitor_records())


def get_setup_group_stats():
    """
    今日改機統計，依機型群組(ESEC/DB/LOC/FC)分組。"改機"(今日已完成)算自
    ee_maintenance_record，只算job_code屬於CED/CEE/CD三類真正改機的紀錄
    (跟get_epoxy_done_by_jcode()同一套篩選標準，理由見該函式docstring—
    e_tag='S'裡混了很多生產中的小動作，不能整批當改機算)；"改機中"/"待改"
    改成算自PM Monitor的即時快照(SETUP/WAIT-SETUP狀態)，比EE Maintenance的
    wait_date/bgn_date推算法準確，是當下真正的狀態，不是歷史推論的。
    回傳{group: {"done": int, "in_progress": int, "waiting": int}}。
    """
    today = datetime.date.today().isoformat()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT machine_id, bgn_date, bgn_time, job_code
        FROM ee_maintenance_record
        WHERE e_tag = 'S' AND end_date = ?
    """, (today,))
    done_rows = cur.fetchall()
    conn.close()

    stats = {g: {"done": 0, "in_progress": 0, "waiting": 0} for g in ("ESEC", "DB", "LOC", "FC")}
    for r in done_rows:
        g = _group_for_machine(r["machine_id"])
        if g and _epoxy_jcode_category(r["job_code"]) is not None:
            stats[g]["done"] += 1

    pm_stats = get_pm_monitor_group_stats()
    for g in ("ESEC", "DB", "LOC", "FC"):
        stats[g]["in_progress"] = pm_stats.get(g, {}).get("SETUP", 0)
        stats[g]["waiting"] = pm_stats.get(g, {}).get("WAIT-SETUP", 0)
    return stats


def _pm_stats_line(label, group_stats, indent=""):
    field_parts = [f"{zh}{group_stats[code]}" for code, zh in _PM_STATUS_LABELS if group_stats.get(code)]
    if not field_parts:
        return None
    return f"{indent}{label}  " + " ".join(field_parts)


def _pm_elapsed_hours(in_time_str, now):
    """PM Monitor的IN TIME是"2026/08/09 17:29"這種格式，算到now經過幾小時。"""
    try:
        dt = datetime.datetime.strptime(in_time_str, "%Y/%m/%d %H:%M")
    except (TypeError, ValueError):
        return None
    return (now - dt).total_seconds() / 3600.0


def _pm_detail_lines(rows, now):
    """PM Monitor機台明細：每一台機台的狀態+目前已等待/進行的時數(IN TIME到現在)+JCODE。"""
    status_zh = dict(_PM_STATUS_LABELS)
    lines = []
    for r in rows:
        elapsed = _pm_elapsed_hours(r["in_time"], now)
        elapsed_txt = f"{elapsed:.2f}hr" if elapsed is not None else "?"
        zh = status_zh.get(r["status"], r["status"])
        jcode_txt = f"  {r['jcode']}" if r["jcode"] else ""
        lines.append(f"{r['entity']}  {zh}  {elapsed_txt}{jcode_txt}")
    return lines


def _pm_overtime_lines(rows, now):
    """
    超時機台：PM Monitor每一列的jcode對應標準工時(PM_JCODE_STD_HOURS)，
    IN TIME到現在的經過時數超過標準就算超時，列出機台代號+經過時數+超時
    時數+jcode+operator工號。
    """
    lines = []
    for r in rows:
        elapsed = _pm_elapsed_hours(r["in_time"], now)
        if elapsed is None:
            continue
        std = get_pm_jcode_std_hours(r["jcode"])
        if std is None or elapsed <= std:
            continue
        operator = r["operator"] or "未指定"
        lines.append(
            f"{r['entity']}  {elapsed:.2f}hr(超時{elapsed - std:.2f}hr)  {r['jcode']}  人員{operator}"
        )
    return lines


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


def build_hourly_push_message(now: datetime.datetime = None) -> str:
    """組出整點推播訊息文字(格式比照同事的推播範本)"""
    if now is None:
        now = datetime.datetime.now()

    title = f"【APG DA 整點推播】{now.strftime('%m/%d %H:%M')}"
    parts = [title]

    # 🔧 今日改機統計：依機型群組(EPOXY=ESEC+DB、LOC、FlipChip)列出今日已完成/
    # 改機中/待改的台數；EPOXY另外逐一列出實際job_code(CED/CEDO/CD...)各自的
    # 台數細項，數字由多到少排序，加總起來要等於EPOXY的改機總數(2026/08/09
    # 使用者要求，方便肉眼核對)。
    setup_stats = get_setup_group_stats()
    esec, db, loc, fc = setup_stats["ESEC"], setup_stats["DB"], setup_stats["LOC"], setup_stats["FC"]
    epoxy = {k: esec[k] + db[k] for k in ("done", "in_progress", "waiting")}
    parts.append("")
    parts.append("🔧 今日改機統計")
    parts.append(_setup_stats_line("EPOXY", epoxy))
    epoxy_jcode = get_epoxy_done_by_jcode()
    jcode_parts = [
        f"{code}{n}台" for code, n in sorted(epoxy_jcode.items(), key=lambda kv: (-kv[1], kv[0]))
        if n
    ]
    if jcode_parts:
        parts.append("  " + " ".join(jcode_parts))
    parts.append(_setup_stats_line("├ESEC", esec, " "))
    parts.append(_setup_stats_line("└DB", db, " "))
    parts.append(_setup_stats_line("LOC", loc))
    parts.append(_setup_stats_line("FlipChip", fc))

    # ⚡ 即時機況：來源是PM/REPAIR/SETUP Monitor頁面的真實快照(不是像上面
    # 「今日改機統計」那樣用EE Maintenance歷史紀錄推算的近似值)。除了依
    # 群組彙總的台數，也列出每台機台的機台號碼+目前狀態+已等待/進行時數
    # (IN TIME到現在)。這個資料源可能還沒抓過或抓取失敗，抓不到資料時
    # 整段跳過，不影響其他推播內容
    pm_rows = get_pm_monitor_records()
    if pm_rows:
        pm_stats = _pm_group_stats_from_rows(pm_rows)
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
            parts.append("機台明細:")
            parts.extend(_pm_detail_lines(pm_rows, now))

    # ⏰ 超時機台：PM Monitor每一列的jcode對應標準工時(PM_JCODE_STD_HOURS)，
    # IN TIME到現在的經過時數超過標準就算超時——是即時判斷，不是像之前那樣
    # 用EE Maintenance歷史紀錄的bgn_date/wait_date推算
    parts.append("")
    parts.append("⏰ 超時機台")
    overtime_lines = _pm_overtime_lines(pm_rows, now) if pm_rows else []
    if overtime_lines:
        parts.extend(overtime_lines)
    else:
        parts.append("(目前無超過標準工時的機台，或PM Monitor資料尚未抓取)")

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
