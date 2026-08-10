# -*- coding: utf-8 -*-
"""
team+ HTTP API 模組(08/06新增)

取代原本用Selenium操控瀏覽器讀/送team+訊息的做法。經同事(APG_TeamplusBot專案)
逆向出team+網頁客戶端實際呼叫的後端API，改用urllib直接發HTTP請求，完全不需要
Edge/Selenium/除錯模式——這條路線這天debug了快一整天都不穩定，改用API後
team+這部分的穩定性應該會好非常多。

原理(與同事teamplus_push.py/teamplus_bot.py的邏輯一致)：
  讀新訊息：POST ChatMainHandler.ashx  action=getNewestMessageList
  送訊息：  POST SendMsgHandler.ashx   action=sendChatMessage
  兩者都靠瀏覽器登入後的Cookie驗證身分，不需要帳密。

用法：
    import teamplus_api
    messages, new_cursor = teamplus_api.read_new_messages(cursor)  # messages: [{"text","batch_id"}, ...]
    ok, desc = teamplus_api.send_message("要送出的文字")          # 只送到機器人推播室
    results = teamplus_api.broadcast_message("要送出的文字")      # 送到機器人推播室+額外聊天室

前置：
    da_bot資料夾下要有 teamplus_cookie.txt，內容是從瀏覽器F12開發者工具->
    網路分頁->任一個team+請求->標頭->要求標頭->cookie 那一整串複製出來的。
    這組cookie有時效性，過期時send_message/read_new_messages會回傳失敗，
    訊息會提示需要重新用F12抓一次新的cookie。

多推播幾個聊天室：
    到目標聊天室畫面，用同樣的F12方式抓出該室的ChatID(網路分頁->送訊息的
    請求->表單資料裡的ChatID欄位)，填進config.txt的teamplus_extra_chat_ids
    (逗號分隔，可以填多個)，broadcast_message()就會一起送。沒設定的話維持
    只送到CHAT_ID(機器人推播室)這一間，行為跟改版前一樣。群組聊天室跟
    跟同事的1對1個人對話都可以填，不用另外標註是哪一種——P2P對話的
    ChatID格式固定是"{自己的Mobile}_{對方Mobile}"(例如"903_1631")，
    程式會自動判斷、換成正確的ChannelType/Recipients(2026/08/10使用者
    實測發現：1對1對話原本用固定的群組ChannelType=1會讀不到/送不出去)。
"""
import os
import re
import sys
import json
import uuid
import ssl
import urllib.request
import urllib.parse

import config

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_PATH = os.path.join(SCRIPT_DIR, "teamplus_cookie.txt")

READ_URL = "https://teamplus.chipmos.com/EIM/Chat/ChatMainHandler.ashx"
SEND_URL = "https://teamplus.chipmos.com/EIM/Common/SendMsgHandler.ashx"
PAGE_URL = "https://teamplus.chipmos.com/EIM/Messenger/MessengerMain.aspx"

# 08/06從F12開發者工具實際抓到、確認可用的「機器人推播」室設定(余毓賢/903自己
# 帳號的預設值)。2026/08/10起這兩個值改成優先讀config.txt的teamplus_mobile／
# teamplus_chat_id，沒設定才 fallback 回這裡的預設值——這樣把整份da_bot資料夾
# 複製給別人用時，對方只要填自己的config.txt，完全不用碰這支程式碼裡的任何一行，
# 就能指到「他自己」的team+帳號代碼跟「他自己」的機器人推播室。
_DEFAULT_MOBILE = "903"
_DEFAULT_CHAT_ID = "702193c7-0029-4b5c-a819-4fa17fdf4f16"
CHANNEL_TYPE = "1"


def _load_cfg_value(key, default):
    try:
        cfg = config.load()
    except FileNotFoundError:
        return default
    return cfg.get(key, "").strip() or default


MOBILE = _load_cfg_value("teamplus_mobile", _DEFAULT_MOBILE)  # 使用者自己帳號的內部代碼，讀/送訊息都要帶
CHAT_ID = _load_cfg_value("teamplus_chat_id", _DEFAULT_CHAT_ID)
RECIPIENTS = [{"Mobile": MOBILE, "Email": ""}]

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# team+的一對一個人對話(P2P)跟群組聊天室(含「機器人推播」這種自己專屬的
# 頻道)，送/讀訊息時要帶的ChannelType跟Recipients不一樣：群組是
# ChannelType=1、Recipients帶自己的Mobile；P2P是ChannelType=0、Recipients
# 要帶「對方」的Mobile。2026/08/10使用者實測發現：額外聊天室裡如果填的是
# 跟同事的1對1對話(例如ChatID"903_1631"，這是APG_DA班長那個人)，用原本
# 寫死的ChannelType=1會讀不到/送不出去。
#
# team+的P2P對話ChatID剛好就是"{我的Mobile}_{對方Mobile}"這種格式(F12
# 實測驗證過)，不需要使用者額外在config.txt裡標註是群組還是P2P，直接從
# ChatID的形狀自動判斷就好。
_P2P_CHAT_ID_RE = re.compile(r"^(\d+)_(\d+)$")


def _channel_info_for_chat(chat_id):
    """依ChatID格式判斷這是群組(ChannelType=1)還是跟某人的1對1對話
    (ChannelType=0，Recipients要換成對方的Mobile)，回傳(channel_type,
    recipients)。判斷不出來(不符合P2P格式，或前半段不是自己的Mobile)一律
    當群組處理，維持原本的行為。"""
    m = _P2P_CHAT_ID_RE.match(chat_id or "")
    if m and m.group(1) == MOBILE:
        return "0", [{"Mobile": m.group(2), "Email": ""}]
    return CHANNEL_TYPE, RECIPIENTS


def load_cookie():
    if not os.path.exists(COOKIE_PATH):
        print(f"[錯誤] 找不到 {COOKIE_PATH}")
        print("       請依說明用F12開發者工具抓team+的cookie，存成這個檔案")
        sys.exit(1)
    with open(COOKIE_PATH, "r", encoding="utf-8") as f:
        cookie = f.read().strip()
    if not cookie:
        print(f"[錯誤] {COOKIE_PATH} 是空的")
        sys.exit(1)
    return cookie


def read_new_messages(cursor=None, chat_id=None):
    """
    讀指定聊天室(預設CHAT_ID，「機器人推播」室)裡比cursor新的訊息。
    cursor是上次讀到的最新BatchID，第一次呼叫可傳None。

    chat_id：2026/08/10使用者要求即時問答也要能在額外聊天室(config.txt
    的teamplus_extra_chat_ids，原本只有broadcast_message()的推播會用到)
    運作，所以這裡開放指定要讀哪一間，不指定時維持原行為(讀CHAT_ID)。

    重要：NewestBatchID傳空字串會被team+的API直接拒絕("參數錯誤：NewestBatchID")，
    這是之前即時問答完全沒反應的真正原因——第一次呼叫永遠失敗，cursor永遠
    初始化不了，之後每一輪都用空字串重蹈覆轍。改成cursor是None時自動產生一個
    隨機UUID當NewestBatchID(格式模仿同事teamplus_bot.py送訊息時自己產生的
    batchID)，實測這樣team+會正常回應IsSuccess=true、視為目前沒有更新的訊息。

    回傳 (messages, new_cursor)，messages是[{"text":內容, "batch_id":該則訊息的
    BatchID}, ...]清單(按時間順序，不是單純文字清單)——呼叫端要靠batch_id
    判斷「這則是不是機器人自己剛送出的」，不能只比對文字內容(機器人自己的
    回覆內容有機會剛好含有查詢關鍵字，例如"downrate"，這種情況只比文字
    會導致機器人把自己的回覆誤判成新指令、觸發下一輪回覆，兩種回覆格式
    來回觸發、自問自答，直到洗版保護的次數上限)。
    如果cookie過期或請求失敗，回傳 ([], cursor)(cursor不變)，並印出錯誤訊息。
    """
    cookie = load_cookie()
    effective_cursor = cursor or str(uuid.uuid4())
    effective_chat_id = chat_id or CHAT_ID
    channel_type, _ = _channel_info_for_chat(effective_chat_id)
    body = urllib.parse.urlencode({
        "action": "getNewestMessageList",
        "ChannelType": channel_type,
        "Mobile": MOBILE,
        "ChatID": effective_chat_id,
        "NewestBatchID": effective_cursor,
        "FromNearline": "false",
        "LoadCount": "25",
    }).encode("utf-8")
    req = urllib.request.Request(READ_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    req.add_header("Accept", "application/json, text/javascript, */*; q=0.01")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", PAGE_URL)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[警告] 讀取team+訊息失敗(可能cookie過期，需要重新用F12抓一組新的): {type(e).__name__}: {e}")
        return [], cursor

    # team+後端這支API實際回傳的清單欄位名稱有時是ChatMessageList、有時是MessageList
    # (同事逆向出來的teamplus_bot.py兩個都有處理)，這裡兩個都要檢查，
    # 只認MessageList的話，如果剛好這次回傳的是ChatMessageList，會每次都誤判成
    # "沒有新訊息"，即時問答會變成永遠沒反應、也不會印出任何錯誤或警告。
    msg_list = data.get("ChatMessageList")
    if msg_list is None:
        msg_list = data.get("MessageList")
    msg_list = msg_list or []
    if not msg_list:
        # 就算這批沒有新訊息，也要記住這次實際用的effective_cursor(尤其是
        # cursor原本是None、剛完成bootstrap的情況)，不能回傳原本的cursor(None)，
        # 否則下一輪又會用None重新產生一個全新的隨機UUID，永遠沒辦法穩定下來、
        # 每次都用不一樣的"起點"去問，行為會變得不可預期。
        return [], effective_cursor

    messages = [
        {"text": m.get("MsgContent", ""), "batch_id": m.get("BatchID")}
        for m in msg_list if m.get("MsgContent")
    ]
    # 用這批訊息裡最大的BatchID當作下次的cursor，避免重複讀到同一批
    new_cursor = effective_cursor
    for m in msg_list:
        bid = m.get("BatchID")
        if bid:
            new_cursor = bid
    return messages, new_cursor


def _load_extra_chat_ids():
    """讀config.txt的teamplus_extra_chat_ids(逗號分隔ChatID清單)，沒設定就回傳空清單。"""
    try:
        cfg = config.load()
    except FileNotFoundError:
        return []
    raw = cfg.get("teamplus_extra_chat_ids", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def all_chat_ids():
    """
    回傳CHAT_ID(機器人推播室)+config.txt裡teamplus_extra_chat_ids設定的所有
    額外聊天室(去重、保留順序)。broadcast_message()推播訊息、
    teamplus_listener.py的即時問答監聽(2026/08/10使用者要求Q&A也要支援
    額外聊天室，不再只有推播)都共用這份清單，同一個地方設定就好。
    """
    seen = []
    for chat_id in [CHAT_ID] + _load_extra_chat_ids():
        if chat_id not in seen:
            seen.append(chat_id)
    return seen


def _send(message, chat_id, batch_id):
    """實際送出訊息的底層邏輯，batch_id由呼叫端決定(送訊息時自己產生的批次ID，
    team+會直接拿這個值當這則訊息的BatchID)。回傳 (ok: bool, desc: str)。"""
    cookie = load_cookie()
    effective_chat_id = chat_id or CHAT_ID
    channel_type, recipients = _channel_info_for_chat(effective_chat_id)
    data = {
        "action": "sendChatMessage",
        "batchID": batch_id,
        "ChannelType": channel_type,
        "ChatID": effective_chat_id,
        "Recipients": json.dumps(recipients, ensure_ascii=False, separators=(",", ":")),
        "GroupList": "[]",
        "MsgContent": message,
        "Content2": "",
        "MsgType": "1",
        "FileList": "",
        "SourceType": "1",
        "AtUsers": "[]",
        "replyBatchID": "",
        "UrlPreviewList": "%5B%5D",
    }
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(SEND_URL, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    req.add_header("Accept", "application/json, text/javascript, */*; q=0.01")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", PAGE_URL)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                 "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
    req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("IsSuccess"):
                return True, "發送成功"
            return False, result.get("Description", "未知錯誤(可能cookie過期，需要重新用F12抓一組新的)")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def send_message(message, chat_id=None):
    """
    送一則訊息到指定聊天室(預設CHAT_ID，「機器人推播」室)。
    回傳 (ok: bool, desc: str)。
    """
    return _send(message, chat_id, str(uuid.uuid4()))


def send_message_get_batch_id(message, chat_id=None):
    """
    跟send_message()一樣送一則訊息，但額外把這次送出實際用的batchID回傳。

    同事逆向出來的teamplus_bot.py開機時，就是靠「先送一則上線通知，拿這則
    訊息真正的batchID當第一個cursor」來啟動監聽，從來不會用空字串或跟訊息
    紀錄無關的隨機值去問team+「目前最新的cursor」。這個函式就是給
    init_listener_state()做同樣的事情用的。

    回傳 (ok: bool, desc: str, batch_id: str)。
    """
    bid = str(uuid.uuid4())
    ok, desc = _send(message, chat_id, bid)
    return ok, desc, bid


SELF_SENT_LOG_PATH = os.path.join(SCRIPT_DIR, "sent_batch_ids.log")
_SELF_SENT_LOG_KEEP = 200


def _record_self_sent_batch_id(bid):
    """
    把自己送出的訊息的BatchID記到硬碟上的檔案(SELF_SENT_LOG_PATH)，跨
    process共用。整點推播(teamplus_push.py，透過run_pipeline.py用
    subprocess執行，是獨立的process)送出的訊息，跟teamplus_listener.py
    (或da_bot_service.py合併服務)的即時問答監聽是不同的process，各自
    在記憶體裡的sent_batch_ids互相看不到——2026/08/10使用者實測發現：
    整點推播的內容剛好含有查詢關鍵字(群組名稱、日期)，監聽端讀回這則
    推播訊息時，因為推播的batchID從來沒被記錄過，被誤判成新指令，自動
    回覆了一則多餘的改機報告。這裡改成推播送出後也把batchID寫進共用檔案，
    監聽端讀訊息時額外檢查這份清單，就能認出「這也是我們自己送的」。

    只保留最後_SELF_SENT_LOG_KEEP筆，避免檔案無限成長；寫檔失敗(例如
    磁碟權限問題)靜默略過，不影響推播本身送出成功與否。
    """
    try:
        with open(SELF_SENT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(bid + "\n")
        with open(SELF_SENT_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > _SELF_SENT_LOG_KEEP:
            with open(SELF_SENT_LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[-_SELF_SENT_LOG_KEEP:])
    except OSError:
        pass


def recent_self_sent_batch_ids():
    """
    回傳_record_self_sent_batch_id()記錄過的BatchID集合，給
    teamplus_listener.py跨process檢查「這則訊息是不是我們自己(不管是
    推播還是問答監聽哪個process)送的」用。檔案不存在/讀取失敗回傳空
    集合，不會讓呼叫端掛掉。
    """
    try:
        with open(SELF_SENT_LOG_PATH, "r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except OSError:
        return set()


def broadcast_message(message):
    """
    送到CHAT_ID(機器人推播室)以及config.txt裡teamplus_extra_chat_ids設定的所有
    額外聊天室。回傳list of (chat_id, ok, desc)，方便呼叫端逐一檢查有沒有哪個
    房間送失敗。沒設定額外聊天室時，效果等同只呼叫send_message(message)一次。

    送出成功的每一則都會把batchID記進recent_self_sent_batch_ids()共用的
    檔案，讓即時問答監聽(通常是不同的process)能認出這是自己推播送出的
    訊息，不會誤判成新指令、自動回覆一則多餘的報告
    (見_record_self_sent_batch_id())。
    """
    results = []
    for chat_id in all_chat_ids():
        ok, desc, bid = send_message_get_batch_id(message, chat_id=chat_id)
        if ok:
            _record_self_sent_batch_id(bid)
        results.append((chat_id, ok, desc))
    return results


if __name__ == "__main__":
    # 簡單測試：python teamplus_api.py 送一則測試訊息(含額外聊天室)
    msg = sys.argv[1] if len(sys.argv) > 1 else "[測試] teamplus_api.py 連線測試"
    for chat_id, ok, desc in broadcast_message(msg):
        print(("✓" if ok else "✗"), f"[{chat_id}]", desc)
