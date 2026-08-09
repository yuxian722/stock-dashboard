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

機器人會記住自己剛送出的回覆內容，讀到跟自己剛講過一樣的話會自動跳過，
不會自問自答、無限循環。

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
import re
import sys
import time
import datetime

import query_bot
import teamplus_api

POLL_INTERVAL_SECONDS = 10


def normalize_for_dedup(s):
    """
    把換行、空白全部拿掉，做為判斷『這則是不是我剛講過的話』的依據，
    避免把自己剛送出的回覆內容當成新指令，自問自答。
    """
    return re.sub(r"\s+", "", s)


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

# 打這些字(整句、不含其他內容)就叫出關鍵字說明清單，忘記怎麼查的時候用
HELP_TRIGGERS = {"查詢", "說明", "help", "指令", "用法", "選單", "?", "？"}

HELP_TEXT = (
    "【DA機器人 查詢關鍵字說明】\n"
    "機台代號(例如BAA08、BA220)可加在關鍵字前後，不用空格也可以：\n"
    "\n"
    "(不加關鍵字)                     完整資訊(即時狀態+統計摘要+稼動率+健康監控)\n"
    "今天                             今天的修機/改機明細\n"
    "昨天                             昨天的修機/改機明細\n"
    "上週 / 上周                      過去7天(不含今天)統計摘要\n"
    "本週 / 本周                      本週一到今天統計摘要\n"
    "07/24~07/30                      指定區間統計摘要\n"
    "稼動 / 稼動率                    最新一筆稼動率資料\n"
    "downrate / down rate / 停機明細  該機台稼動細項(改機/工程/停機/閒置...)\n"
    "健康                             設備健康監控資料\n"
    "\n"
    "機型群組查詢(不用加機台代號)：\n"
    "DB          DB800+DB830+DB700 三組彙總\n"
    "Epoxy       Esec2100+EPOXY(DB) 全部加總\n"
    "EPOXY(DB)   DB700+DB800+DB830 加總\n"
    "CM700       CM700機型群組\n"
    "Esec2100    2100advi+2100SD機型群組\n"
    "\n"
    "官方GROUP彙總表原始數字(不是我們自己逐台平均算的)：\n"
    "<官方群組名稱> + downrate/稼動明細/停機明細，例如「DB800 downrate」\n"
    "官方群組名稱: 2100SD / DATACON8800 / DB700 / DB800 / DB830 /\n"
    "              EPOXY(DB) / Epoxy / Flip Chip / LOC\n"
    "\n"
    "範例: BA220 / BA220今天 / BAA02上週 / BAA08 down rate / DB800downrate\n"
    "\n"
    "打「查詢」「說明」「help」「指令」都可以再叫出這份清單"
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

    # 官方GROUP彙總表數字查詢：「<官方群組名稱> + downrate/稼動明細/停機明細」關鍵字，
    # 回傳CPIS Utilization Analysis頁面最下方GROUP彙總表該群組的官方原始一列數字
    # (跟db_group/EPOXY(DB)/Epoxy等自己逐台平均算出來的數字可能有些微落差，
    # 這裡給的是CPIS官方原始列，供對照驗證用)。必須排在最前面判斷，
    # 否則"DB800 downrate"會先被底下的機台代號規則攔截，當成查機台DB800用。
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
                return {"mode": "group_official_downrate", "group_label": label}

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
            return query_bot.group_official_downrate_reply(cmd["group_label"])
        except Exception as e:
            return f"{cmd['group_label']} 官方GROUP彙總查詢時發生錯誤: {type(e).__name__}: {e}"

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


def init_listener_state():
    """
    初始化監聽狀態(第一次啟動時呼叫一次)。
    透過teamplus_api拿目前最新的訊息游標(cursor)，把當下已存在的訊息都當作
    「已處理」，避免舊訊息被誤觸發回覆；之後每輪呼叫poll_once()時傳入state，
    並會被就地更新，讓da_bot_service.py合併服務也能重用同一套監聽邏輯。
    """
    texts, cursor = teamplus_api.read_new_messages(None)
    print(f"[啟動] 已同步至最新訊息(略過{len(texts)}則既有訊息)，之後只會回應新出現的訊息")
    return {
        "cursor": cursor,
        "bot_sent_norms": [],       # 記住機器人自己最近送出的回覆內容(正規化後)，避免自問自答
        "recent_reply_times": [],   # 防暴衝保護用的時間戳記錄
    }


def poll_once(state):
    """
    檢查一次「機器人推播」室有沒有新訊息，有的話解析、回覆。
    是main()裡while迴圈的其中一輪內容，抽出來讓da_bot_service.py
    合併服務也能在自己的迴圈裡呼叫這個函式，共用同一套邏輯。
    state是init_listener_state()回傳的dict，會被就地更新。
    """
    new_texts, new_cursor = teamplus_api.read_new_messages(state["cursor"])
    if not new_texts:
        return
    state["cursor"] = new_cursor

    bot_sent_norms = state["bot_sent_norms"]
    recent_reply_times = state["recent_reply_times"]

    for text in new_texts:
        norm = normalize_for_dedup(text)
        if norm in bot_sent_norms:
            # 這是機器人自己剛送出的回覆，讀回來了而已，不是新指令，消耗一次記錄後跳過
            bot_sent_norms.remove(norm)
            continue

        cmd = parse_query(text)
        if cmd is None:
            continue

        now = time.time()
        recent_reply_times[:] = [t for t in recent_reply_times if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(recent_reply_times) >= MAX_REPLIES_PER_WINDOW:
            print(f"[緊急停止] {RATE_LIMIT_WINDOW_SECONDS}秒內已回覆{len(recent_reply_times)}次，")
            print("疑似自問自答或異常迴圈，強制停止腳本，請檢查聊天室內容後再重跑")
            sys.exit(1)

        print(f"[收到指令] {text!r} -> {cmd}")
        reply = build_reply(cmd)
        print(f"[回覆] {reply}")
        ok, desc = teamplus_api.send_message(reply)
        if ok:
            recent_reply_times.append(now)
            bot_sent_norms.append(normalize_for_dedup(reply))
            if len(bot_sent_norms) > 30:
                bot_sent_norms.pop(0)
        else:
            print(f"[警告] 送出訊息失敗: {desc}")


def main():
    """
    獨立執行teamplus_listener.py時的進入點(只做即時問答，不含整點推播)。
    整點推播+即時問答合併執行請改用 da_bot_service.py。
    """
    state = init_listener_state()
    print(f"[監聽中] 每 {POLL_INTERVAL_SECONDS} 秒檢查一次「機器人推播」室有沒有新訊息，Ctrl+C 結束")

    while True:
        time.sleep(POLL_INTERVAL_SECONDS)
        poll_once(state)


if __name__ == "__main__":
    main()
