"""
team+「機器人推播」室 - 即時問答監聽腳本 (08/06改版：HTTP API，不需要Edge/Selenium)

用法: 在「機器人推播」對話畫面打任何含有機台代號的訊息，機器人會自動回覆。
      不需要「查」開頭，例如「BA220」「BA220今天」「BAA02今天狀態」都可以。
      忘記關鍵字怎麼打的話，直接打「查詢」或「help」，機器人會回完整的
      關鍵字說明清單(見下方HELP_TEXT)。

指令關鍵字(可加在機台代號前後，不用空格也可以):
    (不加關鍵字)      -> 完整資訊(即時狀態+修機/改機統計摘要+最新稼動率+設備健康監控)
    今天              -> 今天的修機/改機明細
    昨天              -> 昨天的修機/改機明細
    上週              -> 過去7天(不含今天)的統計摘要
    本週              -> 本週一到今天的統計摘要
    07/24~07/30       -> 指定區間的統計摘要
    稼動              -> 最新一筆稼動率資料
    查詢 / help       -> 叫出關鍵字說明清單(HELP_TEXT)

範例: BA220 / BA220今天 / 查BA220上週 / BAA02稼動

機器人會記住自己送出的每則訊息的BatchID，讀到自己剛送出的訊息會自動跳過，
不會自問自答、無限循環(用BatchID而不是比對文字內容，因為回覆內容本身有機會
剛好含有查詢關鍵字，只比文字會誤判成新指令、觸發下一輪回覆)。

════════════════════════════════════════
08/06重大改版：改用team+ HTTP API(teamplus_api.py)，不再用Selenium操控Edge
════════════════════════════════════════
原本這支腳本靠附身模式Edge讀取畫面上的DOM內容、模擬打字送出，這條路線
在公司網路環境下反覆出現除錯模式Edge啟動失敗的問題，除錯了一整天。
改用同事逆向出來的team+後端HTTP API後，完全不需要Edge/Selenium，
只要teamplus_cookie.txt裡的cookie有效即可運作，穩定性好非常多。

前置: da_bot資料夾下要有 teamplus_cookie.txt(見teamplus_api.py開頭說明)。
      不再需要開除錯模式Edge、不再需要msedgedriver.exe(仍保留給CPIS爬蟲用)。
      執行: python teamplus_listener.py，Ctrl+C結束監聽。
"""
import sys

# Windows主控台預設用cp950(繁體中文)編碼，HELP_TEXT等回覆內容含emoji(🤖等)，
# print()會直接丟UnicodeEncodeError把腳本弄當掉。改成把stdout/stderr強制用
# utf-8輸出，encode不了的字元用errors="replace"跳過。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import re
import time
import datetime

import query_bot
import teamplus_api
import singleton_lock

POLL_INTERVAL_SECONDS = 10


# ---------- 指令解析 ----------

MACHINE_RE = re.compile(r"([A-Za-z]{1,4}\d{2,4})")

# CPIS Utilization Analysis 頁面最下方「GROUP」彙總表官方群組名稱，
# 對應query_bot.OFFICIAL_GROUP_LABELS，用來辨識「<官方群組名稱>+downrate關鍵字」
# 這種要查官方原始彙總數字(而非我們自己算的平均)的訊息
_OFFICIAL_GROUP_LABELS = [
    "2100SD", "DATACON8800", "DB700", "DB800", "DB830",
    "EPOXY(DB)", "Epoxy", "Flip Chip", "LOC",
]


def _build_official_group_pattern(label):
    escaped = re.escape(label).replace(r"\ ", r"\s*")
    return re.compile(r"(?<![A-Za-z0-9])" + escaped + r"(?![A-Za-z0-9])", re.IGNORECASE)


_OFFICIAL_GROUP_PATTERNS = [(label, _build_official_group_pattern(label)) for label in _OFFICIAL_GROUP_LABELS]

# downrate關鍵字判斷全部共用這一份，避免像"DOWN RATE"(中間有空格)這種寫法
# 在某一處判斷式裡漏比對到(容忍空格、大小寫都要跟這裡一致)
_DOWNRATE_KW_RE = re.compile(r"down\s*rate|停機明細|稼動明細", re.IGNORECASE)

# 「<機型群組>改機」查詢(例如"DB改機"、"ESEC改機")：今日該群組改機明細，
# 包含台數、CED/CEE/CD分類平均工時、依人員(工號)分類的台數+平均工時
# (2026/08/09使用者要求)。key是使用者輸入時比對用的字樣，value是
# query_bot.group_changeover_detail_reply()認得的內部群組代號——一定要跟
# hourly_push._group_for_machine()回傳的值一致(ESEC/DB/LOC/FC)，FlipChip
# 機台回傳的是"FC"不是"FlipChip"/"FLIPCHIP"，這裡不能對到錯的代號，不然
# 查詢永遠是空的
_CHANGEOVER_GROUP_KEYWORDS = [
    ("EPOXY", "EPOXY"), ("ESEC", "ESEC"), ("DB", "DB"),
    ("LOC", "LOC"), ("FLIP CHIP", "FC"), ("FLIPCHIP", "FC"), ("FC", "FC"),
]


def _build_changeover_group_pattern(label):
    escaped = re.escape(label).replace(r"\ ", r"\s*")
    return re.compile(r"(?<![A-Za-z0-9])" + escaped + r"\s*改機(?![A-Za-z0-9])", re.IGNORECASE)


_CHANGEOVER_GROUP_PATTERNS = [
    (internal, _build_changeover_group_pattern(label)) for label, internal in _CHANGEOVER_GROUP_KEYWORDS
]

# 「工時」查詢(例如"s10435工時"、"27512總工時")：今日該工號人員的修機+改機
# 總工時(2026/08/09使用者要求)。工號格式不固定(純數字或字母開頭+數字)，
# 用寬鬆一點的樣式抓緊貼在"工時"前面的那一段
_WORKHOURS_RE = re.compile(r"(?:總)?工時")
_WORKHOURS_ENGINEER_RE = re.compile(r"([A-Za-z]?\d{4,6})\s*(?:總)?工時")

# 只打群組關鍵字、沒加「改機」兩個字時(例如"8/9 DB")，一定要搭配日期才
# 觸發成當天改機彙總查詢，不然裸的"DB"要維持原本查即時彙總(db_group_reply)
# 的行為，這裡跟_CHANGEOVER_GROUP_PATTERNS共用同一份關鍵字清單，只是不
# 要求後面接"改機"兩個字。
_CHANGEOVER_GROUP_BARE_PATTERNS = [
    (internal, _build_official_group_pattern(label)) for label, internal in _CHANGEOVER_GROUP_KEYWORDS
]

# 「8/9」這種指定日期(月/日，跟班別對齊)，可以加在「工時」「改機」「<群組>改機」
# 這幾種查詢前後，改成查那一天的資料而不是預設的今日(2026/08/10使用者要求)。
# 負向前瞻排除掉"8/9~8/10"這種區間寫法(區間是機台查詢既有的功能，不要互相干擾)。
_SINGLE_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})(?!\s*[~\-])")


def _resolve_query_date(m):
    """把_SINGLE_DATE_RE比對到的"8/9"這種字串轉成該班別日中午的datetime
    (用中午是確保_shift_day_bounds()一定落在這個日期，不會因為訊息比對到
    的時間點卡在07:30以前被誤判成前一天)。月份/日期不存在(例如"13/40")就
    回傳None，呼叫端要能優雅忽略掉、當作沒抓到日期。"""
    try:
        mo, d = int(m.group(1)), int(m.group(2))
        year = datetime.date.today().year
        return datetime.datetime(year, mo, d, 12, 0)
    except ValueError:
        return None

# 打這些字(整句、不含其他內容)就叫出關鍵字說明清單，忘記怎麼查的時候用
HELP_TRIGGERS = {"查詢", "說明", "help", "指令", "用法", "選單", "?", "？"}

HELP_TEXT = (
    "🤖 DA機器人 查詢指令（機台代號可加在關鍵字前後，不用空格也可以）：\n"
    "• 機台代號 → 完整資訊（即時狀態＋統計摘要＋稼動率＋健康監控，例：BAA08）\n"
    "• 機台代號＋今天 / 昨天 → 當天修機/改機明細\n"
    "• 機台代號＋上週 / 上周 → 過去7天（不含今天）統計摘要\n"
    "• 機台代號＋本週 / 本周 → 本週一到今天統計摘要\n"
    "• 機台代號＋07/24~07/30 → 指定區間統計摘要\n"
    "• 機台代號＋稼動 / 稼動率 → 最新一筆稼動率資料\n"
    "• 機台代號＋downrate / down rate / 停機明細 → 該機台稼動細項（改機/工程/停機/閒置...）\n"
    "• 機台代號＋健康 → 設備健康監控資料\n"
    "\n"
    "機型群組查詢（不用加機台代號）：\n"
    "• DB → DB800+DB830+DB700 三組彙總\n"
    "• Epoxy → Esec2100+EPOXY(DB) 全部加總\n"
    "• EPOXY(DB) → DB700+DB800+DB830 加總\n"
    "• CM700 → CM700機型群組\n"
    "• Esec2100 → 2100advi+2100SD機型群組\n"
    "\n"
    "改機明細/工時查詢：\n"
    "• <群組>改機 → 今日該群組改機台數＋CED/CEE/CD分類平均工時＋依人員(工號)分類明細\n"
    "  群組：EPOXY(=ESEC+DB) / ESEC / DB / LOC / FlipChip，例：DB改機\n"
    "• 改機（不加群組）→ 列出今日EPOXY/LOC/FlipChip全部群組的改機彙總\n"
    "• <工號>工時 → 該工號今日修機＋改機總工時，例：s10435工時\n"
    "• 工時（不加工號） → 列出今日所有有紀錄工號的總工時\n"
    "• 以上三種前面/後面可以加「8/9」這種日期(跟英文字母中間留個空格)，改查\n"
    "  指定那一天，例：8/9 DB改機／8/9工時／8/9 DB\n"
    "\n"
    "官方GROUP彙總表原始數字（CPIS Utilization Analysis頁面原始列，不是我們自己逐台平均算的）：\n"
    "• <官方群組名稱>＋downrate/稼動明細/停機明細 → 例：DB800 downrate\n"
    "  官方群組名稱：2100SD / DATACON8800 / DB700 / DB800 / DB830 / EPOXY(DB) / Epoxy / Flip Chip / LOC\n"
    "• downrate（不加群組）→ 列出全部官方群組的downrate彙總\n"
    "• 以上可以加「8/9」這種日期，改查指定那一天，例：8/9 DB800downrate／8/9 down rate\n"
    "\n"
    "範例：BA220／BA220今天／BAA02上週／BAA08 down rate／DB800downrate\n"
    "\n"
    "• 查詢 / 說明 / help / 指令 / 用法 / 選單 → 顯示本說明"
)


def parse_query(text):
    """
    解析訊息，只要句子裡出現機台代號(英文字母+數字)就當作查詢指令，
    不再要求「查」開頭。再從整句掃描時間/稼動關鍵字決定要回哪種資訊，
    沒抓到關鍵字就預設回即時狀態(涵蓋「故障」「狀態」這類詞)。
    回傳 dict，或 None(這句話裡完全沒有機台代號，不是查詢)
    """
    text = text.strip()
    if not text:
        return None

    if text.lower() in HELP_TRIGGERS:
        return {"mode": "help"}

    # 「8/9」這種指定日期，可以搭配「<群組>改機」「改機」「工時」查詢
    # (2026/08/10使用者要求)。這裡先抓出來，下面幾種模式各自決定要不要用。
    m_date = _SINGLE_DATE_RE.search(text)
    query_now = _resolve_query_date(m_date) if m_date else None
    date_label = f"{query_now.month:02d}/{query_now.day:02d}" if query_now else None
    date_ymd = query_now.strftime("%Y%m%d") if query_now else None

    # 「<機型群組>改機」查詢(例如"DB改機"、"8/9DB改機")：指定日期(預設今日)
    # 該群組改機明細(台數+CED/CEE/CD分類平均工時+依人員分類的台數跟平均
    # 工時，2026/08/09使用者要求)。必須排在最前面判斷，否則"DB改機"會先
    # 被後面「DB」單獨出現的規則攔截，變成觸發db_group彙總查詢而不是這裡
    # 的改機明細查詢
    for internal, pattern in _CHANGEOVER_GROUP_PATTERNS:
        if pattern.search(text):
            cmd = {"mode": "group_changeover_detail", "group_name": internal}
            if query_now is not None:
                cmd["now"], cmd["date_label"] = query_now, date_label
            return cmd

    # 只打日期+群組關鍵字、沒加「改機」兩個字(例如"8/9 DB")：等同查那天
    # 的<群組>改機彙總(2026/08/10使用者要求)。一定要先抓到日期才觸發，不然
    # 裸的"DB"要維持原本查即時彙總(db_group_reply)的行為，不能被這裡攔截掉
    if query_now is not None:
        for internal, pattern in _CHANGEOVER_GROUP_BARE_PATTERNS:
            if pattern.search(text):
                return {"mode": "group_changeover_detail", "group_name": internal,
                        "now": query_now, "date_label": date_label}

    # 沒指定群組的「改機」查詢(例如"8/9改機"，或單獨打"改機")：列出EPOXY/LOC/
    # FlipChip全部群組指定日期(預設今日)的改機彙總(2026/08/10使用者要求)。
    # 排在上面兩種「有指定群組」的判斷之後，這裡才是真的沒抓到群組關鍵字。
    if "改機" in text:
        cmd = {"mode": "all_changeover"}
        if query_now is not None:
            cmd["now"], cmd["date_label"] = query_now, date_label
        return cmd

    # 「工時」查詢(例如"s10435工時"、"27512總工時"、"8/9工時"，或單獨打"工時"
    # 列出今天所有人員)：指定日期(預設今日)該工號人員的修機+改機總工時
    # (2026/08/09使用者要求)。也要排在機台代號規則前面，避免"s10435"這種
    # 字串被誤判成機台代號
    if _WORKHOURS_RE.search(text):
        m = _WORKHOURS_ENGINEER_RE.search(text)
        cmd = {"mode": "workhours", "engineer_id": m.group(1) if m else None}
        if query_now is not None:
            cmd["now"], cmd["date_label"] = query_now, date_label
        return cmd

    # 官方GROUP彙總表數字查詢：「<官方群組名稱> + downrate/稼動明細/停機明細」關鍵字，
    # 回傳CPIS Utilization Analysis頁面最下方GROUP彙總表該群組的官方原始一列數字
    # (跟db_group/EPOXY(DB)/Epoxy等自己逐台平均算出來的數字可能有些微落差，
    # 這裡給的是CPIS官方原始列，供對照驗證用)。必須排在最前面判斷，
    # 否則"DB800 downrate"會先被底下的機台代號規則攔截，當成查機台DB800用。
    # 可以搭配「8/9」這種日期查指定那一天的資料(2026/08/10使用者要求)。
    #
    # 先把關鍵字本身從文字裡拿掉(換成空格)再比對群組名稱，是為了處理
    # 「DB800downrate」這種中間沒空格的寫法：關鍵字緊貼著群組名稱時，
    # 群組名稱右邊的英文字母會讓邊界判斷失敗，拿掉關鍵字後邊界就正常了。
    stripped = text
    has_downrate_kw = bool(_DOWNRATE_KW_RE.search(stripped))
    if has_downrate_kw:
        stripped = _DOWNRATE_KW_RE.sub(" ", stripped)
        for label, pattern in _OFFICIAL_GROUP_PATTERNS:
            if pattern.search(stripped):
                cmd = {"mode": "group_official_downrate", "group_label": label}
                if query_now is not None:
                    cmd["date_ymd"], cmd["date_label"] = date_ymd, date_label
                return cmd

        # 沒比對到任何特定官方群組名稱、也沒有機台代號(例如單獨"downrate"、
        # "8/9 down rate")：回傳全部官方GROUP的彙總(2026/08/10使用者要求)。
        # 一定要先排除掉機台代號存在的情況，不然"BAA08 downrate"這種既有的
        # 單機查詢會被這裡攔截掉，變成回全部官方群組而不是BAA08自己的資料
        if not MACHINE_RE.search(stripped):
            cmd = {"mode": "all_groups_official_downrate"}
            if query_now is not None:
                cmd["date_ymd"], cmd["date_label"] = date_ymd, date_label
            return cmd

    # 「EPOXY(DB)」出現(含括號)時，觸發EPOXY(DB)機型群組查詢(=DB700+DB800+DB830)
    # 必須排在單獨的「Epoxy」判斷跟後面的「DB」判斷之前，
    # 否則"EPOXY(DB)"裡的"DB"或"Epoxy"字樣會先被其他規則攔截、判斷成別的群組
    if re.search(r"epoxy\s*\(\s*db\s*\)", text, re.IGNORECASE):
        return {"mode": "db_group", "group_names": ["EPOXY(DB)"]}

    # 單獨的「Epoxy」(後面沒接括號)時，觸發Epoxy機型群組查詢
    # (=Esec2100(2100advi+2100SD) + EPOXY(DB)(DB700+DB800+DB830) 全部加總)
    if re.search(r"(?<![A-Za-z0-9])epoxy(?![A-Za-z0-9(（])", text, re.IGNORECASE):
        return {"mode": "db_group", "group_names": ["Epoxy"]}

    # 「CM700」單獨出現(前後不接英數字)時，觸發CM700機型群組查詢
    # (CM700+CM700X已合併定義在query_bot.MODEL_GROUPS的"CM700"這個key下)
    if re.search(r"(?<![A-Za-z0-9])CM700(?![A-Za-z0-9])", text, re.IGNORECASE):
        return {"mode": "db_group", "group_names": ["CM700"]}

    # 「Esec2100」單獨出現時，觸發Esec2100機型群組查詢
    # (2100advi+2100SD已合併定義在query_bot.MODEL_GROUPS的"Esec2100"這個key下)
    if re.search(r"(?<![A-Za-z0-9])Esec2100(?![A-Za-z0-9])", text, re.IGNORECASE):
        return {"mode": "db_group", "group_names": ["Esec2100"]}

    # 「DB」單獨出現(前後不接英數字，例如不是DB800的一部分)時，
    # 觸發DB800/DB830/DB700三組機型的群組查詢，優先判斷，不用抓機台代號
    if re.search(r"(?<![A-Za-z0-9])DB(?![A-Za-z0-9])", text, re.IGNORECASE):
        return {"mode": "db_group"}

    m = MACHINE_RE.search(text)
    if not m:
        return None
    machine = m.group(1).upper()

    today = datetime.date.today()

    if "稼動" in text or "稼動率" in text:
        return {"machine": machine, "mode": "util"}

    if _DOWNRATE_KW_RE.search(text):
        return {"machine": machine, "mode": "downrate"}

    if "健康" in text:
        return {"machine": machine, "mode": "health"}

    if "上週" in text or "上周" in text:
        end = today - datetime.timedelta(days=1)
        start = today - datetime.timedelta(days=7)
        return {"machine": machine, "mode": "range",
                "date_start": start.isoformat(), "date_end": end.isoformat()}

    if "本週" in text or "本周" in text:
        start = today - datetime.timedelta(days=today.weekday())
        return {"machine": machine, "mode": "range",
                "date_start": start.isoformat(), "date_end": today.isoformat()}

    if "昨天" in text:
        d = today - datetime.timedelta(days=1)
        return {"machine": machine, "mode": "detail", "date": d.isoformat()}

    if "今天" in text:
        return {"machine": machine, "mode": "detail", "date": today.isoformat()}

    m2 = re.search(r"(\d{1,2})/(\d{1,2})[~\-](\d{1,2})/(\d{1,2})", text)
    if m2:
        mo1, d1, mo2, d2 = map(int, m2.groups())
        year = today.year
        try:
            start = datetime.date(year, mo1, d1)
            end = datetime.date(year, mo2, d2)
            return {"machine": machine, "mode": "range",
                    "date_start": start.isoformat(), "date_end": end.isoformat()}
        except ValueError:
            pass

    # 沒抓到時間相關關鍵字(包含「故障」「狀態」這類詞)，預設回完整資訊
    return {"machine": machine, "mode": "full"}


def build_reply(cmd):
    mode = cmd["mode"]

    if mode == "help":
        return HELP_TEXT

    if mode == "db_group":
        try:
            return query_bot.db_group_reply(cmd.get("group_names"))
        except Exception as e:
            return f"群組查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "group_official_downrate":
        try:
            return query_bot.group_official_downrate_reply(
                cmd["group_label"], date_ymd=cmd.get("date_ymd"), date_label=cmd.get("date_label")
            )
        except Exception as e:
            return f"{cmd['group_label']} 官方GROUP彙總查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "all_groups_official_downrate":
        try:
            return query_bot.all_groups_official_downrate_reply(
                date_ymd=cmd.get("date_ymd"), date_label=cmd.get("date_label")
            )
        except Exception as e:
            return f"官方GROUP彙總查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "group_changeover_detail":
        try:
            return query_bot.group_changeover_detail_reply(
                cmd["group_name"], now=cmd.get("now"), date_label=cmd.get("date_label")
            )
        except Exception as e:
            return f"{cmd['group_name']}改機查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "all_changeover":
        try:
            return query_bot.all_changeover_reply(now=cmd.get("now"), date_label=cmd.get("date_label"))
        except Exception as e:
            return f"改機查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "workhours":
        try:
            return query_bot.workhours_reply(
                cmd.get("engineer_id"), now=cmd.get("now"), date_label=cmd.get("date_label")
            )
        except Exception as e:
            return f"工時查詢時發生錯誤: {type(e).__name__}: {e}"

    machine = cmd["machine"]

    try:
        if mode == "full":
            return query_bot.full_info_reply(machine)
        if mode == "detail":
            return query_bot.detail_reply(machine, date=cmd["date"])
        if mode == "range":
            return query_bot.summary_reply_range(machine, cmd["date_start"], cmd["date_end"])
        if mode == "util":
            return query_bot.utilization_reply(machine)
        if mode == "downrate":
            return query_bot.downrate_reply(machine)
        if mode == "health":
            return query_bot.health_reply(machine)
    except Exception as e:
        return f"{machine} 查詢時發生錯誤: {type(e).__name__}: {e}"

    return f"不支援的查詢模式: {mode}"


# ---------- 主流程 ----------

# 防暴衝保護：短時間內送太多次回覆就自動停止，不管是不是自問自答的bug
# 或其他未預期狀況，都不會無限洗版下去(拉到模組層級，方便da_bot_service.py共用)
MAX_REPLIES_PER_WINDOW = 8
RATE_LIMIT_WINDOW_SECONDS = 60


BOOT_MESSAGE = "🤖 DA機器人已上線，輸入「查詢」看關鍵字說明"


def init_listener_state():
    """
    初始化監聽狀態(第一次啟動時呼叫一次)。

    重要：team+的getNewestMessageList這支API，NewestBatchID傳空字串/隨便產生
    一個跟訊息紀錄無關的值，都不保證能拿到正確、穩定可用的cursor(甚至可能直接
    被拒絕、參數錯誤)。同事逆向出來的teamplus_bot.py開機時的做法，是先送一則
    「已上線」的訊息，用這則訊息真正的batchID(team+親自確認、真實存在於訊息
    紀錄裡的值)當第一個cursor，之後的訊息只要比這個batchID新就一定抓得到。
    這裡照做，不再靠讀取空cursor去猜「目前最新」是什麼。

    如果連上線通知都送失敗(例如cookie過期)，退回用read_new_messages(None)
    (teamplus_api內部會自動用隨機UUID當NewestBatchID，至少不會直接卡死)，
    讓服務還能啟動、之後靠[警告]訊息提示需要重新抓cookie。
    """
    ok, desc, bid = teamplus_api.send_message_get_batch_id(BOOT_MESSAGE)
    sent_batch_ids = []
    if ok:
        cursor = bid
        sent_batch_ids.append(bid)
        print("[啟動] 已送出上線通知，之後只會回應這則之後才出現的新訊息")
    else:
        print(f"[警告] 上線通知送出失敗({desc})，改用備援方式啟動")
        messages, cursor = teamplus_api.read_new_messages(None)
        print(f"[啟動] 已同步至最新訊息(略過{len(messages)}則既有訊息)，之後只會回應新出現的訊息")
    return {
        "cursor": cursor,
        "sent_batch_ids": sent_batch_ids,  # 記住機器人自己送出的訊息的BatchID，避免自問自答
        "recent_reply_times": [],          # 防暴衝保護用的時間戳記錄
    }


def poll_once(state):
    """
    檢查一次「機器人推播」室有沒有新訊息，有的話解析、回覆。
    是main()裡while迴圈的其中一輪內容，抽出來讓da_bot_service.py
    合併服務也能在自己的迴圈裡呼叫這個函式，共用同一套邏輯。
    state是init_listener_state()回傳的dict，會被就地更新。
    """
    new_messages, new_cursor = teamplus_api.read_new_messages(state["cursor"])
    if not new_messages:
        return
    state["cursor"] = new_cursor

    sent_batch_ids = state["sent_batch_ids"]
    recent_reply_times = state["recent_reply_times"]

    for msg in new_messages:
        text = msg["text"]
        bid = msg["batch_id"]

        # 用BatchID(送訊息當下自己產生、team+確實記錄下來的識別碼)判斷「這是不是
        # 自己剛送出的訊息」，不能只比對文字內容——機器人的回覆內容本身有機會
        # 剛好含有查詢關鍵字(例如"downrate"、"EPOXY(DB)")，只比文字的話，
        # 機器人會把自己的回覆誤判成新指令、再回一次，兩種回覆格式來回觸發、
        # 自問自答，直到洗版保護的次數上限才停下來(同事的teamplus_bot.py
        # 就是靠BatchID識別、不是比對文字，這裡照做)。
        if bid and bid in sent_batch_ids:
            sent_batch_ids.remove(bid)
            continue

        cmd = parse_query(text)
        if cmd is None:
            continue

        now = time.time()
        recent_reply_times[:] = [t for t in recent_reply_times if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(recent_reply_times) >= MAX_REPLIES_PER_WINDOW:
            # 這裡以前是sys.exit(1)：一遇到疑似自問自答/洗版就把整支服務(連同整點推播)
            # 一起殺掉，之後除非有人發現、手動重開，不然機器人會一直保持沒反應的狀態。
            # 改成只跳過這批訊息剩下的部分不回覆，讓服務繼續跑，等這波次數退到
            # RATE_LIMIT_WINDOW_SECONDS之外自動恢復正常回覆。
            print(f"[警告] {RATE_LIMIT_WINDOW_SECONDS}秒內已回覆{len(recent_reply_times)}次，"
                  "疑似自問自答或異常迴圈，這批訊息剩下的部分先不回覆，服務繼續運作")
            break

        print(f"[收到指令] {text!r} -> {cmd}")
        reply = build_reply(cmd)
        print(f"[回覆] {reply}")
        ok, desc, reply_bid = teamplus_api.send_message_get_batch_id(reply)
        if ok:
            recent_reply_times.append(now)
            sent_batch_ids.append(reply_bid)
            if len(sent_batch_ids) > 30:
                sent_batch_ids.pop(0)
        else:
            print(f"[警告] 送出訊息失敗: {desc}")


def main():
    """
    獨立執行teamplus_listener.py時的進入點(只做即時問答，不含整點推播)。
    整點推播+即時問答合併執行請改用 da_bot_service.py。
    """
    singleton_lock.acquire_or_exit()
    state = init_listener_state()
    print(f"[監聽中] 每 {POLL_INTERVAL_SECONDS} 秒檢查一次「機器人推播」室有沒有新訊息，Ctrl+C 結束")

    while True:
        time.sleep(POLL_INTERVAL_SECONDS)
        poll_once(state)


if __name__ == "__main__":
    main()
