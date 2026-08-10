# -*- coding: utf-8 -*-
"""手動驗證用小工具：直接呼叫我們自己的程式碼實際發送給team+ API的HTTP
請求，印出完整的原始回應，診斷1對1個人對話(P2P，例如"APG_DA班長"，
ChatID"903_1631")讀不到訊息的問題。

背景：sendChatMessage(送訊息)這條路已經確認修好、能正常送到P2P聊天室，
但getNewestMessageList(讀訊息)用同一組ChatID("903_1631")一直讀不到任何
新訊息，即使team+網頁上該則訊息已經顯示「已讀」。反覆用F12在瀏覽器端
抓封包也找不到team+前端在P2P對話開著的時候呼叫過getNewestMessageList
(推測前端是靠WebSocket/即時推播接收P2P訊息，不是靠這支API)，所以沒有
「範例」封包可以比對。

懷疑的root cause：send用的"903_1631"，格式是"{自己Mobile}_{對方Mobile}"，
很可能只是前端拿來組路由/表單用的「假ChatID」，sendChatMessage這支API
本身是靠Recipients(對方Mobile)解析出真正要送到哪個P2P對話，不見得真的
需要正確ChatID；但getNewestMessageList可能真的需要team+資料庫裡這個
P2P對話「真正」的ChatID(可能是GUID，跟群組聊天室一樣)，而不是這組
自己组的假ChatID。getOneOnOneChatInfo這支API(team+前端開啟P2P對話時
會呼叫、抓對話的中繼資料)回應裡也有一個"ChatID"欄位，這個才有可能是
真正的、getNewestMessageList吃得進去的ChatID。

這支腳本做三件事，各自印出完整原始JSON：
  1. 呼叫getOneOnOneChatInfo，看看它回傳的ChatID等欄位長什麼樣子
  2. 呼叫getNewestMessageList，ChatID用目前程式碼在用的"903_1631"(對照組)
  3. 如果步驟1回傳的ChatID跟"903_1631"不一樣，再呼叫一次
     getNewestMessageList，這次ChatID換成步驟1抓到的值(驗證懷疑是否正確)

用法(要在能連到team+、且teamplus_cookie.txt是最新的機器上執行)：
    python _probe_p2p_chat.py 903_1631
    python _probe_p2p_chat.py 903_1631 1631    # 指定對方Mobile(預設從ChatID猜)
"""
import sys
import json
import uuid
import urllib.request
import urllib.parse

import teamplus_api


def _post(url, data, label):
    cookie = teamplus_api.load_cookie()
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    req.add_header("Accept", "application/json, text/javascript, */*; q=0.01")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", teamplus_api.PAGE_URL)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    req.add_header("Cookie", cookie)
    print(f"\n===== {label} =====")
    print(f"送出的參數: {data}")
    try:
        with urllib.request.urlopen(req, context=teamplus_api._SSL_CTX, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        print(f"[錯誤] 請求失敗: {type(e).__name__}: {e}")
        return None
    print("原始回應內容:")
    print(raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print("[警告] 回應不是合法JSON，上面已印出原始內容")
        return None
    print("解析後(縮排格式):")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    return parsed


def main():
    if len(sys.argv) < 2:
        print(f"用法: python {sys.argv[0]} <ChatID，例如903_1631> [對方Mobile，不填就從ChatID猜]")
        sys.exit(1)

    chat_id = sys.argv[1]
    if len(sys.argv) >= 3:
        other_mobile = sys.argv[2]
    else:
        parts = chat_id.split("_")
        other_mobile = parts[-1] if len(parts) == 2 else None

    if other_mobile is None:
        print(f"[錯誤] 無法從ChatID {chat_id!r} 猜出對方Mobile，請手動指定第二個參數")
        sys.exit(1)

    print(f"目標ChatID={chat_id}, 對方Mobile={other_mobile}, 自己Mobile={teamplus_api.MOBILE}")

    # 步驟1：getOneOnOneChatInfo，看team+眼中這個P2P對話「真正」的中繼資料長怎樣
    info = _post(
        teamplus_api.READ_URL,
        {"action": "getOneOnOneChatInfo", "mobile": other_mobile},
        "步驟1: getOneOnOneChatInfo",
    )

    real_chat_id = None
    if info:
        # 不確定實際欄位名稱是ChatID還是其他大小寫/命名方式，這裡盡量多猜幾種，
        # 印出來讓人眼確認，也方便之後對照。
        for candidate_key in ("ChatID", "ChatId", "chatID", "chatId"):
            if candidate_key in info and info[candidate_key]:
                real_chat_id = info[candidate_key]
                print(f"\n從getOneOnOneChatInfo找到疑似真正ChatID欄位 {candidate_key} = {real_chat_id!r}")
                break
        if real_chat_id is None:
            print("\n[注意] 沒有在回應裡自動找到ChatID欄位，"
                  "請人工檢查上面印出的完整JSON，看看有沒有其他像GUID的欄位")

    # 步驟2：getNewestMessageList，ChatID用目前程式碼實際在用的值(對照組)
    channel_type, _ = teamplus_api._channel_info_for_chat(chat_id)
    _post(
        teamplus_api.READ_URL,
        {
            "action": "getNewestMessageList",
            "ChannelType": channel_type,
            "Mobile": teamplus_api.MOBILE,
            "ChatID": chat_id,
            "NewestBatchID": str(uuid.uuid4()),
            "FromNearline": "false",
            "LoadCount": "25",
        },
        f"步驟2: getNewestMessageList (ChatID=目前用的假ID {chat_id!r})",
    )

    # 步驟3：如果步驟1找到了不一樣的「真正」ChatID，用它再試一次getNewestMessageList
    if real_chat_id and real_chat_id != chat_id:
        _post(
            teamplus_api.READ_URL,
            {
                "action": "getNewestMessageList",
                "ChannelType": channel_type,
                "Mobile": teamplus_api.MOBILE,
                "ChatID": real_chat_id,
                "NewestBatchID": str(uuid.uuid4()),
                "FromNearline": "false",
                "LoadCount": "25",
            },
            f"步驟3: getNewestMessageList (ChatID=getOneOnOneChatInfo回傳的真正ID {real_chat_id!r})",
        )
    elif real_chat_id == chat_id:
        print(f"\n[注意] getOneOnOneChatInfo回傳的ChatID跟目前用的{chat_id!r}相同，"
              "表示ChatID本身不是問題根源，要往其他方向(例如ChannelType、Mobile欄位)排查")

    print("\n=== 診斷完成，請把上面全部輸出內容截圖/複製傳回 ===")


if __name__ == "__main__":
    main()
