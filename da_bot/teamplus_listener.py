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
2026/08/10：即時問答支援多聊天室
════════════════════════════════════════
原本即時問答只在CHAT_ID(「機器人推播」室)運作。現在改成跟teamplus_push.py
的推播共用同一份聊天室清單(config.txt的teamplus_extra_chat_ids，逗號分隔)，
在這些額外聊天室裡問問題，機器人也會在同一間聊天室回覆。每個聊天室各自有
獨立的cursor/自問自答保護狀態，不會互相干擾。每個聊天室開機時都會貼一則
「已上線」通知、拿真實batchID當cursor(原本額外聊天室想改用不留言的靜默
同步方式，但2026/08/10使用者實測發現那樣cursor不可靠，之後在那間聊天室
打的訊息永遠讀不到，所以全部聊天室統一用送訊息這條可靠的路)。

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
import threading

import query_bot
import teamplus_api
import singleton_lock
import shift_query

POLL_INTERVAL_SECONDS = 10


# ---------- 指令解析 ----------

MACHINE_RE = re.compile(r"([A-Za-z]{1,4}\d{2,4})")

# 「<機台代號>改機」要求機台代號緊鄰"改機"(可留空白)，不能像"改機"在文字
# 某處出現、機台代號在文字另一處出現這樣各自獨立成立就觸發——這正是
# 2026/08/10發現的"ACON8800"自問自答案例的根因(見下面parse_query裡的
# 詳細說明)。前面加(?<![A-Za-z0-9])避免從更長的英數字串中間擷取出子字串
# (例如"DATACON8800"裡的"ACON8800")。
_MACHINE_CHANGEOVER_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,4}\d{2,4})\s*改機(?![A-Za-z0-9])", re.IGNORECASE)

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
# 查詢永遠是空的。"2100"/"CM700"是2026/08/10使用者額外要求的別名，分別
# 對到ESEC(Esec2100機型群組跟改機內部群組"ESEC"是同一批BA2/BA4開頭機台)、
# LOC(CM700機型群組跟改機內部群組"LOC"是同一批BA8開頭機台，見query_bot.
# MODEL_GROUPS的機台清單定義)，不是新的獨立群組。
_CHANGEOVER_GROUP_KEYWORDS = [
    ("EPOXY", "EPOXY"), ("ESEC", "ESEC"), ("2100", "ESEC"), ("DB", "DB"),
    ("LOC", "LOC"), ("CM700", "LOC"), ("FLIP CHIP", "FC"), ("FLIPCHIP", "FC"), ("FC", "FC"),
]


def _build_group_suffix_pattern(label, suffix):
    """跟_build_official_group_pattern()類似，但關鍵字尾巴要接固定的動作字樣
    (改機/修機/產品)，不是單純比對群組名稱本身。"""
    escaped = re.escape(label).replace(r"\ ", r"\s*")
    return re.compile(r"(?<![A-Za-z0-9])" + escaped + r"\s*" + suffix + r"(?![A-Za-z0-9])", re.IGNORECASE)


_CHANGEOVER_GROUP_PATTERNS = [
    (internal, _build_group_suffix_pattern(label, "改機")) for label, internal in _CHANGEOVER_GROUP_KEYWORDS
]

# 「<機型群組>修機」查詢(例如"2100修機"、"2100 修機")：今日該群組依修機
# code分類的次數統計，每個code底下再列出各機台各自修了幾次(2026/08/10
# 使用者要求)。跟_CHANGEOVER_GROUP_PATTERNS共用同一份群組別名清單，只是
# 動作字樣換成「修機」。
_REPAIR_GROUP_PATTERNS = [
    (internal, _build_group_suffix_pattern(label, "修機")) for label, internal in _CHANGEOVER_GROUP_KEYWORDS
]

# 「<機型群組>產品」查詢(例如"DB產品"、"2100產品"、"LOC產品")：列出該群組
# 每台機台目前是「加熱」還是「畫膠」產品(2026/08/10使用者要求)。同樣共用
# 群組別名清單，動作字樣換成「產品」。
_PRODUCT_GROUP_PATTERNS = [
    (internal, _build_group_suffix_pattern(label, "產品")) for label, internal in _CHANGEOVER_GROUP_KEYWORDS
]


# 「<群組> <班別> 改機」查詢(例如"2100 AD改機"、"8/11 2100 AD改機")：
# CPIS EE Maintenance Record報表的Shift(AD/AN/BD/BN，A/B班組×早/夜班，
# 2026/08/10使用者截圖確認)是查詢時的過濾參數，不是本地資料庫裡存的
# 欄位——這種查詢要即時連線CPIS查(見shift_query.py)，不是查本地DB，
# 一定要排在_GROUP_REPAIR_CODE_PATTERNS前面判斷："2100 AD改機"如果沒被
# 這裡先攔下來，會被下面「<群組> <修機代碼>」那組規則搶走(AD剛好也符合
# [A-Z]{1,8}的代碼形狀)，變成去查「AD」這個修機代碼、而不是觸發班別
# 改機查詢。
_SHIFT_CODES = ("AD", "AN", "BD", "BN")


def _build_group_shift_pattern(label):
    escaped = re.escape(label).replace(r"\ ", r"\s*")
    shift_alt = "|".join(_SHIFT_CODES)
    return re.compile(
        r"(?<![A-Za-z0-9])(?i:" + escaped + r")\s+(" + shift_alt + r")\s*改機(?![A-Za-z0-9])"
    )


_GROUP_SHIFT_CHANGEOVER_PATTERNS = [
    (internal, _build_group_shift_pattern(label)) for label, internal in _CHANGEOVER_GROUP_KEYWORDS
]


def _build_group_code_pattern(label):
    """「<群組> <修機代碼>」查詢(例如"2100 BWD")：群組名稱後面直接接一個
    全大寫的代碼，不用"修機"這種動作字樣(2026/08/10使用者要求)。群組名稱
    比對不分大小寫(?i:...)，但代碼部分要求全大寫[A-Z]——不能整個pattern
    都用re.IGNORECASE，不然任何隨口打的英文單字(例如"DB downrate"的
    "downrate"，雖然它本身已經被更前面的downrate判斷式攔截，這裡是額外
    保險)都會被誤判成代碼查詢，全大寫的要求大幅降低誤判機率。"""
    escaped = re.escape(label).replace(r"\ ", r"\s*")
    return re.compile(r"(?<![A-Za-z0-9])(?i:" + escaped + r")\s+([A-Z]{1,8})(?![A-Za-z0-9])")


_GROUP_REPAIR_CODE_PATTERNS = [
    (internal, _build_group_code_pattern(label)) for label, internal in _CHANGEOVER_GROUP_KEYWORDS
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
    "• 機台代號 → 完整資訊（即時狀態＋統計摘要＋稼動率，例：BAA08）\n"
    "• 機台代號＋今天 / 昨天 → 當天修機/改機明細\n"
    "• 機台代號＋上週 / 上周 → 過去7天（不含今天）統計摘要\n"
    "• 機台代號＋本週 / 本周 → 本週一到今天統計摘要\n"
    "• 機台代號＋07/24~07/30 → 指定區間統計摘要\n"
    "• 機台代號＋稼動 / 稼動率 → 最新一筆稼動率資料\n"
    "• 機台代號＋downrate / down rate / 停機明細 → 該機台稼動細項（改機/工程/停機/閒置...）\n"
    "• 機台代號＋機況 / 即時機況 → 該機台即時狀態（PM Monitor真實快照）\n"
    "\n"
    "機型群組查詢（不用加機台代號）：\n"
    "• DB → DB800+DB830+DB700 三組彙總\n"
    "• Epoxy → Esec2100+EPOXY(DB) 全部加總\n"
    "• EPOXY(DB) → DB700+DB800+DB830 加總\n"
    "• CM700 → CM700機型群組\n"
    "• Esec2100 → 2100advi+2100SD機型群組\n"
    "• 機況 / 即時機況（不加機台代號）→ 全公司PM Monitor即時機況總覽（分組台數＋機台明細＋超時機台）\n"
    "\n"
    "改機明細/工時查詢：\n"
    "• <群組>改機 → 今日該群組改機台數＋CED/CEE/CD分類平均工時＋依人員(工號)分類明細＋\n"
    "  逐台機台明細(待改時間＋改機時間＋人員工號)\n"
    "  群組：EPOXY(=ESEC+DB) / ESEC(=2100) / DB / LOC(=CM700) / FlipChip，例：DB改機／2100改機／CM700改機\n"
    "• 改機（不加群組）→ 列出今日EPOXY/LOC/FlipChip全部群組的改機彙總\n"
    "• <機台代號>改機 → 該機台改機次數＋分類平均改機時間＋待改時間(即時＋歷史平均)＋改機人員，例：BAA02改機\n"
    "  加「歷史」→ 不限日期，查這台機台全部歷史紀錄，例：BAA02改機歷史\n"
    "• <工號>工時 → 該工號今日修機＋改機總工時，例：s10435工時\n"
    "• 工時（不加工號） → 列出今日所有有紀錄工號的總工時\n"
    "• <群組>修機 → 今日該群組依修機代碼(code)分類的次數統計，每個代碼底下再列出\n"
    "  各機台各自修了幾次，例：2100修機／2100 修機\n"
    "• <群組> <修機代碼>(代碼要大寫) → 只看單一代碼：共修幾次＋wait repair總時數＋\n"
    "  in repair總時數＋有修過的機台號碼，例：2100 BWD／DB BWD\n"
    "• <群組>產品 → 該群組每台機台目前是「加熱」還是「畫膠」產品＋Product ID＋B/D\n"
    "  (依最後一次真正改機判斷，不是當天限定)，例：DB產品／2100產品／LOC產品(LOC全部都是加熱)\n"
    "• <群組> <班別>改機 → 指定班別(AD=A班早班／AN=A班夜班／BD=B班早班／BN=B班夜班)\n"
    "  的改機明細，即時向CPIS查詢(不是查本地資料，要等幾秒)，沒指定日期預設查今天，\n"
    "  例：2100 AD改機／8/11 2100 AD改機／DB BN改機\n"
    "• 以上（機台改機歷史、群組產品、群組班別改機除外）前面/後面可以加「8/9」這種日期\n"
    "  (跟英文字母中間留個空格)，\n"
    "  改查指定那一天，例：8/9 DB改機／8/9工時／8/9 DB／8/9 BAA02改機\n"
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

    # 「<機型群組>修機」查詢(例如"2100修機"、"2100 修機")：指定日期(預設
    # 今日)該群組依修機code分類的次數統計+每個code底下各機台的次數
    # (2026/08/10使用者要求)。要排在上面「改機」判斷之後，避免動作字樣
    # 判斷順序反過來影響到彼此(兩者關鍵字不同不會真的衝突，但保持一致)
    for internal, pattern in _REPAIR_GROUP_PATTERNS:
        if pattern.search(text):
            cmd = {"mode": "group_repair_detail", "group_name": internal}
            if query_now is not None:
                cmd["now"], cmd["date_label"] = query_now, date_label
            return cmd

    # 「<機型群組>產品」查詢(例如"DB產品"、"2100產品"、"LOC產品")：列出該
    # 群組每台機台目前是「加熱」還是「畫膠」產品(2026/08/10使用者要求)。
    # 不支援指定日期——查的是機台「目前」的產品設定(最後一次真正改機決定
    # 的，不是當天限定)，沒有「今日」的概念可以切換。
    for internal, pattern in _PRODUCT_GROUP_PATTERNS:
        if pattern.search(text):
            return {"mode": "group_product_type", "group_name": internal}

    # 「<機型群組> <班別> 改機」查詢(例如"2100 AD改機"、"8/11 2100 AD改機")：
    # 即時查CPIS的特定班別(AD/AN/BD/BN)改機資料(2026/08/10使用者要求，見
    # shift_query.py開頭說明)。要排在「<群組> <修機代碼>」判斷之前，不然
    # "AD"會先被那條規則當成修機代碼截走。沒指定日期預設今日。
    for internal, pattern in _GROUP_SHIFT_CHANGEOVER_PATTERNS:
        m_shift = pattern.search(text)
        if m_shift:
            resolved_ymd = date_ymd or datetime.date.today().strftime("%Y%m%d")
            resolved_label = date_label or datetime.date.today().strftime("%m/%d")
            return {
                "mode": "live_group_shift_changeover", "group_name": internal,
                "shift": m_shift.group(1).upper(), "date_ymd": resolved_ymd, "date_label": resolved_label,
            }

    # 「<機型群組> <修機代碼>」查詢(例如"2100 BWD")：只看單一修機代碼的
    # 統計，不用"修機"這種動作字樣(2026/08/10使用者要求)。程式碼部分要求
    # 全大寫[A-Z]，跟中文動作字樣(修機/改機/產品)天生不會衝突，也不會誤觸
    # 到"DB downrate"這種既有查詢(downrate是小寫，_GROUP_REPAIR_CODE_
    # PATTERNS比對不到)。可以搭配"8/9"這種日期。
    for internal, pattern in _GROUP_REPAIR_CODE_PATTERNS:
        m_code = pattern.search(text)
        if m_code:
            cmd = {"mode": "group_repair_code_detail", "group_name": internal, "code": m_code.group(1)}
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

    # 「<機台代號>改機」查詢(例如"BAA02改機"、"BAA02改機歷史")：單一機台
    # 改機次數+平均改機時間+待改時間+改機人員(2026/08/10使用者要求)。要排
    # 在上面「群組+改機」判斷之後(機台代號不會誤觸群組判斷，不影響順序)、
    # 也要排在下面「沒指定群組的改機」判斷之前——不然"BAA02改機"會被那條
    # 規則搶走，變成回全部群組彙總而不是BAA02自己的資料。「歷史」關鍵字
    # 切換成不限日期查全部歷史紀錄，沒加就是今日(可以搭配"8/9"這種日期)。
    #
    # 2026/08/10發現真實自問自答案例：原本這裡是「"改機"在text裡」+「text
    # 裡任何地方出現機台代號」兩個獨立條件(不要求相鄰)，導致機器人自己回覆
    # 的"ACON8800查無所屬機型群組，無法判斷改機標準"這種錯誤訊息(機台代號
    # 錯誤解析自"DATACON8800"官方群組名稱裡的子字串，"改機"兩字則來自訊息
    # 本文)又被自己讀回去、誤判成新的"ACON8800改機"查詢，無限循環下去。
    # 改用_MACHINE_CHANGEOVER_RE要求機台代號跟"改機"緊鄰(可留空白)，
    # 跟_build_group_suffix_pattern()對群組關鍵字的嚴謹度一致，不能像
    # 這樣讓兩個獨立條件各自在文字不同地方成立就觸發。
    m_machine_changeover = _MACHINE_CHANGEOVER_RE.search(text)
    if m_machine_changeover:
        cmd = {"mode": "machine_changeover_detail", "machine": m_machine_changeover.group(1).upper(),
               "all_history": "歷史" in text}
        if query_now is not None:
            cmd["now"], cmd["date_label"] = query_now, date_label
        return cmd

    # 沒指定群組的「改機」查詢(例如"8/9改機"，或單獨打"改機")：列出EPOXY/LOC/
    # FlipChip全部群組指定日期(預設今日)的改機彙總(2026/08/10使用者要求)。
    # 排在上面兩種「有指定群組」的判斷之後，這裡才是真的沒抓到群組關鍵字。
    if "改機" in text:
        cmd = {"mode": "all_changeover"}
        if query_now is not None:
            cmd["now"], cmd["date_label"] = query_now, date_label
        return cmd

    # 「機況」查詢，不加機台代號(例如單獨打"機況"、"即時機況")：全公司PM
    # Monitor即時機況總覽(2026/08/10使用者要求新增「即時機況查詢」)。要排在
    # 沒有機台代號這個條件成立時才觸發，不然"BA220機況"這種有指定機台的
    # 寫法會被這裡攔截掉，變成查全部機況而不是BA220自己的即時狀態
    # (那種情況留給下面掃到機台代號之後的"機況"關鍵字判斷處理)。
    if "機況" in text and not MACHINE_RE.search(text):
        return {"mode": "all_live_status"}

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

    # 「<機台代號>機況」/「<機台代號>即時機況」：單一機台目前即時狀態
    # (2026/08/10使用者要求新增「即時機況查詢」)，要排在_DOWNRATE_KW_RE前面，
    # 兩者關鍵字不衝突但保持跟其他單一機台關鍵字判斷的排列順序一致
    if "機況" in text:
        return {"machine": machine, "mode": "live"}

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

    if mode == "group_repair_detail":
        try:
            return query_bot.group_repair_detail_reply(
                cmd["group_name"], now=cmd.get("now"), date_label=cmd.get("date_label")
            )
        except Exception as e:
            return f"{cmd['group_name']}修機查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "group_repair_code_detail":
        try:
            return query_bot.group_repair_code_detail_reply(
                cmd["group_name"], cmd["code"], now=cmd.get("now"), date_label=cmd.get("date_label")
            )
        except Exception as e:
            return f"{cmd['group_name']} {cmd['code']}修機查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "live_group_shift_changeover":
        # 正式流程走_poll_room_once()裡的背景執行緒(即時查CPIS要幾十秒，
        # 不能卡住問答主迴圈)，這裡是給直接呼叫build_reply()的情境用的
        # 同步版本(例如測試、或未來其他呼叫端)，行為上等同直接拿到最終
        # 結果，不會有中間的「查詢中」提示。
        try:
            return shift_query.live_group_shift_changeover_reply(
                cmd["group_name"], cmd["shift"], cmd["date_ymd"], cmd["date_label"]
            )
        except Exception as e:
            return f"{cmd['group_name']} {cmd['shift']}改機即時查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "group_product_type":
        try:
            return query_bot.group_product_type_reply(cmd["group_name"])
        except Exception as e:
            return f"{cmd['group_name']}產品查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "all_changeover":
        try:
            return query_bot.all_changeover_reply(now=cmd.get("now"), date_label=cmd.get("date_label"))
        except Exception as e:
            return f"改機查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "machine_changeover_detail":
        try:
            return query_bot.machine_changeover_detail_reply(
                cmd["machine"], now=cmd.get("now"), date_label=cmd.get("date_label"),
                all_history=cmd.get("all_history", False)
            )
        except Exception as e:
            return f"{cmd['machine']}改機查詢時發生錯誤: {type(e).__name__}: {e}"

    if mode == "all_live_status":
        try:
            return query_bot.all_live_status_reply()
        except Exception as e:
            return f"即時機況查詢時發生錯誤: {type(e).__name__}: {e}"

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
        if mode == "live":
            return query_bot.live_status_reply(machine)
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


def _init_room_state(chat_id):
    """
    初始化單一聊天室的監聽狀態(cursor/sent_batch_ids/recent_reply_times)。

    一律用「先送一則已上線訊息，拿這則訊息真正的batchID(team+親自確認、
    真實存在於訊息紀錄裡的值)當第一個cursor」開機——team+的
    getNewestMessageList這支API，NewestBatchID傳空字串/隨便產生一個跟
    訊息紀錄無關的值，都不保證能拿到正確、穩定可用的cursor(甚至可能直接
    被拒絕、參數錯誤，或後續read_new_messages()永遠讀不到任何新訊息)，
    這是同事逆向出來的teamplus_bot.py驗證過可靠的開機方式。

    2026/08/10使用者實測發現：額外聊天室原本為了不留言、改用「靜默同步
    (read_new_messages(None)，內部用隨機UUID當cursor)」開機，結果變成
    那兩間聊天室之後打的訊息永遠讀不到——team+對這種跟訊息紀錄無關的
    隨機cursor顯然無法正確判斷「這之後有沒有新訊息」，會議記錄可靠性 >
    避免多貼一行「已上線」，所以全部聊天室統一改回送訊息拿真實batchID
    這條路。

    如果上線通知送失敗(例如cookie過期)，退回用read_new_messages(None)，
    讓服務還能啟動、之後靠[警告]訊息提示需要重新抓cookie。
    """
    ok, desc, bid = teamplus_api.send_message_get_batch_id(BOOT_MESSAGE, chat_id=chat_id)
    if ok:
        print(f"[啟動] 聊天室{chat_id}已送出上線通知，之後只會回應這則之後才出現的新訊息")
        return {
            "cursor": bid,
            "sent_batch_ids": [bid],  # 記住機器人自己送出的訊息的BatchID，避免自問自答
            "recent_reply_times": [],  # 防暴衝保護用的時間戳記錄
            "last_query_text": None,  # 內容型防迴圈用：上一次觸發查詢的文字
            "same_text_streak": 0,  # 同一段文字連續觸發查詢的次數
        }
    print(f"[警告] 聊天室{chat_id}上線通知送出失敗({desc})，改用備援方式啟動")
    messages, cursor = teamplus_api.read_new_messages(None, chat_id=chat_id)
    print(f"[啟動] 聊天室{chat_id}已同步至最新訊息(略過{len(messages)}則既有訊息)，之後只會回應新出現的訊息")
    return {"cursor": cursor, "sent_batch_ids": [], "recent_reply_times": [],
            "last_query_text": None, "same_text_streak": 0}


def init_listener_state():
    """
    初始化監聽狀態(第一次啟動時呼叫一次)。

    對teamplus_api.all_chat_ids()回傳的每一間聊天室各自初始化獨立的
    cursor/sent_batch_ids/recent_reply_times(2026/08/10使用者要求即時
    問答不再只在CHAT_ID運作，要能在額外聊天室也回答問題)，每間聊天室
    互不干擾——不會因為A聊天室洗版就影響B聊天室的回覆額度，也不會把
    A聊天室的自己人訊息誤判成B聊天室的新指令。

    回傳{"rooms": {chat_id: {cursor/sent_batch_ids/recent_reply_times}, ...}}。
    """
    return {"rooms": {chat_id: _init_room_state(chat_id) for chat_id in teamplus_api.all_chat_ids()}}


def _handle_live_shift_query_async(chat_id, cmd):
    """
    在背景執行緒實際執行班別(AD/AN/BD/BN)改機的即時CPIS查詢，完成後把
    結果送回同一個聊天室(2026/08/10使用者要求)。這支函式本身要對所有
    例外負責(不能讓背景執行緒不明不白地死掉、使用者永遠等不到回覆)，
    shift_query.live_group_shift_changeover_reply()內部已經包了CPIS連線
    失敗的處理，這裡多包一層是防呆(萬一有沒預期到的例外，也要讓使用者
    知道查詢失敗，而不是石沉大海)。

    送出結果後要記到跨process共用的sent_batch_ids.log(_record_self_sent_
    batch_id())，理由跟teamplus_api.broadcast_message()一樣：這個回覆內容
    包含群組名稱+"改機"，讀回去時可能被自己的規則誤判成新查詢，不記錄
    的話會自問自答(這裡走的是背景執行緒直接送訊息，不是經過_poll_room_
    once()主流程那個in-memory的sent_batch_ids，所以要靠這份跨process
    記錄，跟監聽主流程共用同一套判斷)。
    """
    try:
        reply = shift_query.live_group_shift_changeover_reply(
            cmd["group_name"], cmd["shift"], cmd["date_ymd"], cmd["date_label"]
        )
    except Exception as e:
        reply = f"{cmd['group_name']} {cmd['shift']}改機即時查詢時發生未預期錯誤: {type(e).__name__}: {e}"

    print(f"[聊天室{chat_id}][背景班別查詢完成][回覆] {reply}")
    ok, desc, reply_bid = teamplus_api.send_message_get_batch_id(reply, chat_id=chat_id)
    if ok:
        teamplus_api._record_self_sent_batch_id(reply_bid)
    else:
        print(f"[警告] 聊天室{chat_id}背景班別查詢結果送出失敗: {desc}")


def _poll_room_once(chat_id, room_state):
    """
    檢查一次指定聊天室有沒有新訊息，有的話解析、回覆(回覆會送回同一間
    聊天室)。room_state是_init_room_state()回傳的dict，會被就地更新。
    """
    new_messages, new_cursor = teamplus_api.read_new_messages(room_state["cursor"], chat_id=chat_id)
    if not new_messages:
        return
    room_state["cursor"] = new_cursor

    sent_batch_ids = room_state["sent_batch_ids"]
    recent_reply_times = room_state["recent_reply_times"]
    # 整點推播(teamplus_push.py)是獨立的process(run_pipeline.py用subprocess
    # 執行)，跟這裡的sent_batch_ids是不同process的記憶體，互相看不到彼此
    # 送出的訊息。2026/08/10使用者實測發現：推播內容剛好含有查詢關鍵字
    # (群組名稱/日期)，讀回自己的推播訊息時被誤判成新指令、多回了一則
    # 報告。額外檢查這份跨process共用的記錄(見teamplus_api.broadcast_message())。
    cross_process_sent_ids = teamplus_api.recent_self_sent_batch_ids()

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
        if bid and bid in cross_process_sent_ids:
            continue

        cmd = parse_query(text)
        if cmd is None:
            continue

        # 內容型防迴圈：不管BatchID比對出於什麼原因失效(例如2026/08/10發現
        # 的"ACON8800"案例——機器人自己的錯誤訊息剛好又能被解析成新查詢，
        # 一路循環到洗版保護的次數上限)，只要「同一段文字」連續觸發查詢
        # 超過3次，就先靜音這段文字、不再回覆，等下一段不一樣的文字出現才
        # 恢復——這是比對「內容」而不是BatchID，能攔住BatchID機制本身
        # 出問題的情況，是最後一道防線(使用者2026/08/10要求)。
        if text == room_state.get("last_query_text"):
            room_state["same_text_streak"] = room_state.get("same_text_streak", 0) + 1
        else:
            room_state["last_query_text"] = text
            room_state["same_text_streak"] = 1
        if room_state["same_text_streak"] > 3:
            print(f"[警告] 聊天室{chat_id} 同一段文字連續觸發查詢第{room_state['same_text_streak']}次: "
                  f"{text!r}，疑似自問自答迴圈，暫停回覆這段文字，直到出現不同內容為止")
            continue

        now = time.time()
        recent_reply_times[:] = [t for t in recent_reply_times if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(recent_reply_times) >= MAX_REPLIES_PER_WINDOW:
            # 這裡以前是sys.exit(1)：一遇到疑似自問自答/洗版就把整支服務(連同整點推播)
            # 一起殺掉，之後除非有人發現、手動重開，不然機器人會一直保持沒反應的狀態。
            # 改成只跳過這批訊息剩下的部分不回覆，讓服務繼續跑，等這波次數退到
            # RATE_LIMIT_WINDOW_SECONDS之外自動恢復正常回覆。
            print(f"[警告] 聊天室{chat_id} {RATE_LIMIT_WINDOW_SECONDS}秒內已回覆{len(recent_reply_times)}次，"
                  "疑似自問自答或異常迴圈，這批訊息剩下的部分先不回覆，服務繼續運作")
            break

        print(f"[聊天室{chat_id}][收到指令] {text!r} -> {cmd}")

        # 班別(AD/AN/BD/BN)改機查詢是即時查CPIS(2026/08/10使用者要求，見
        # shift_query.py開頭說明：不進整點排程，問的當下才即時抓)，實測
        # 其他CPIS報表要花幾十秒，不能像其他模式一樣同步呼叫build_reply()
        # 卡住這個迴圈——那樣會讓「其他房間」「這個房間的其他查詢」全部
        # 卡住等這一次CPIS查詢跑完，等於重蹈2026/08/09整點任務卡住問答
        # 主迴圈那個bug的覆轍(da_bot_service.py後來把整點任務丟背景執行緒
        # 就是同一個理由)。這裡先送一則「查詢中」提示(且刻意不在提示文字
        # 裡緊鄰放group+shift+"改機"，避免讀回時又被自己的規則誤判成新
        # 查詢)，實際查詢丟到背景執行緒，查完才把結果送回同一個聊天室。
        if cmd["mode"] == "live_group_shift_changeover":
            ack = "🔄 查詢中，這是即時向CPIS查詢的班別資料，請稍候幾秒..."
            print(f"[聊天室{chat_id}][回覆(即時查詢中)] {ack}")
            ok, desc, ack_bid = teamplus_api.send_message_get_batch_id(ack, chat_id=chat_id)
            if ok:
                recent_reply_times.append(now)
                sent_batch_ids.append(ack_bid)
                if len(sent_batch_ids) > 30:
                    sent_batch_ids.pop(0)
                threading.Thread(
                    target=_handle_live_shift_query_async, args=(chat_id, cmd), daemon=True
                ).start()
            else:
                print(f"[警告] 聊天室{chat_id}送出「查詢中」提示失敗: {desc}")
            continue

        reply = build_reply(cmd)
        print(f"[聊天室{chat_id}][回覆] {reply}")
        ok, desc, reply_bid = teamplus_api.send_message_get_batch_id(reply, chat_id=chat_id)
        if ok:
            recent_reply_times.append(now)
            sent_batch_ids.append(reply_bid)
            if len(sent_batch_ids) > 30:
                sent_batch_ids.pop(0)
        else:
            print(f"[警告] 聊天室{chat_id}送出訊息失敗: {desc}")


def poll_once(state):
    """
    檢查一輪所有聊天室有沒有新訊息，有的話解析、回覆。
    是main()裡while迴圈的其中一輪內容，抽出來讓da_bot_service.py
    合併服務也能在自己的迴圈裡呼叫這個函式，共用同一套邏輯。
    state是init_listener_state()回傳的dict(含"rooms")，會被就地更新。
    """
    for chat_id, room_state in state["rooms"].items():
        _poll_room_once(chat_id, room_state)


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
