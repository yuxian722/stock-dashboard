# -*- coding: utf-8 -*-
"""手動驗證用小工具：直接呼叫我們自己的程式碼實際發送給team+ API的HTTP
請求，印出完整的原始回應，診斷1對1個人對話(P2P，例如"APG_DA班長"，
ChatID"903_1631")讀不到訊息的問題。

第一版(v1)先驗證了「懷疑getNewestMessageList要用getOneOnOneChatInfo
回傳的『真正』ChatID，而不是送訊息用的903_1631格式」這個假設——結果
getOneOnOneChatInfo回傳的ChatID也是"903_1631"，跟現在用的一樣，
表示ChatID格式不是問題根源。

v2改用完全比照teamplus_listener.py實際運作方式的真實cursor(送一則訊息拿
真正的batchID，而不是隨機UUID)重測，結果證實：就算用真實cursor、
使用者也確實在聊天室打了字，read_new_messages()還是回傳空的messages。
這代表問題不是cursor bootstrap方式，而是更底層的東西。

v2的read_new_messages()只印出「解析後」的結果(messages/new_cursor)，
沒印出team+實際回傳的原始JSON——v3補上這塊：直接發送跟
read_new_messages()完全一樣的原始HTTP請求，把team+真正回傳的內容整個
印出來。結果：回應格式是單純的平面結構(LastServerDate/MessageList/
IsSuccess/Description，沒有像getOneOnOneChatInfo那樣包一層"Data")，
排除了巢狀結構的懷疑；但用真實cursor、使用者也確實打了字，
MessageList還是空的、Description是"查無資料"。

這一版(v4)換個懷疑方向：ChannelType=0這個參數。這是_channel_info_for_chat()
依ChatID格式("{自己Mobile}_{對方Mobile}")自動判斷出來的，這個判斷邏輯
是從sendChatMessage(送訊息)那邊逆推、驗證過的——但getNewestMessageList
(讀訊息)是完全不同的一支API，不能假設兩者對ChannelType的定義/要求一樣。
很有可能讀訊息這支API根本不區分P2P/群組，一律都要傳ChannelType=1
(把ChatID本身當作已經足夠識別是哪個對話)，我們卻依樣畫葫蘆傳了
ChannelType=0，導致team+內部把這次查詢解讀成別的意思、找不到對應的
訊息紀錄。這一版額外用同一個cursor、但把ChannelType強制改成"1"
再讀一次，兩相對照即可驗證。

用法：
    python _probe_p2p_chat.py 903_1631
"""
import sys
import json
import urllib.request
import urllib.parse

import teamplus_api


def _raw_get_newest_message_list(chat_id, cursor, channel_type_override=None):
    cookie = teamplus_api.load_cookie()
    if channel_type_override is not None:
        channel_type = channel_type_override
    else:
        channel_type, _ = teamplus_api._channel_info_for_chat(chat_id)
    body = urllib.parse.urlencode({
        "action": "getNewestMessageList",
        "ChannelType": channel_type,
        "Mobile": teamplus_api.MOBILE,
        "ChatID": chat_id,
        "NewestBatchID": cursor,
        "FromNearline": "false",
        "LoadCount": "25",
    }).encode("utf-8")
    req = urllib.request.Request(teamplus_api.READ_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    req.add_header("Accept", "application/json, text/javascript, */*; q=0.01")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", teamplus_api.PAGE_URL)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, context=teamplus_api._SSL_CTX, timeout=15) as resp:
        return resp.read().decode("utf-8")


def main():
    chat_id = sys.argv[1] if len(sys.argv) > 1 else "903_1631"
    print(f"目標聊天室 ChatID={chat_id}")

    print("\n===== 步驟1: 送一則訊息，建立『真實』cursor起點 =====")
    ok, desc, bid = teamplus_api.send_message_get_batch_id(
        "[診斷] _probe_p2p_chat.py 測試開始，等一下請在這個聊天室輸入任何文字", chat_id=chat_id
    )
    print(f"送出結果: ok={ok}, desc={desc!r}, batch_id={bid!r}")
    if not ok:
        print("[錯誤] 連送出訊息都失敗了，後面的讀取測試沒有意義，先確認cookie/ChatID有沒有問題")
        sys.exit(1)

    input("\n>>> 請現在切到team+，在「APG_DA班長」(或你指定的那個)聊天室裡輸入任何文字並送出，"
          "完成後回來這裡按 Enter 繼續... ")

    print("\n===== 步驟2: 直接發送原始getNewestMessageList請求(不經過我們的解析邏輯) =====")
    print(f"cursor={bid!r}")
    try:
        raw = _raw_get_newest_message_list(chat_id, bid)
    except Exception as e:
        print(f"[錯誤] 請求失敗: {type(e).__name__}: {e}")
        sys.exit(1)
    print("原始回應內容(team+實際回傳的，完全沒經過我們程式碼處理):")
    print(raw)
    try:
        parsed = json.loads(raw)
        print("解析後(縮排格式，方便看有幾層):")
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print("[警告] 回應不是合法JSON")

    print("\n===== 步驟3: 用同一個cursor呼叫我們正式程式碼的read_new_messages() =====")
    print("(這一步跟teamplus_listener.py的_poll_room_once()做的事完全一樣，用來對照步驟2的原始內容)")
    messages, new_cursor = teamplus_api.read_new_messages(bid, chat_id=chat_id)
    print(f"messages = {json.dumps(messages, ensure_ascii=False, indent=2)}")
    print(f"new_cursor = {new_cursor!r}")

    print("\n===== 步驟4: 同一個cursor，但把ChannelType強制改成\"1\"(群組聊天室用的值)再讀一次 =====")
    print("(懷疑getNewestMessageList這支API讀訊息時其實不分P2P/群組，一律該用ChannelType=1)")
    try:
        raw2 = _raw_get_newest_message_list(chat_id, bid, channel_type_override="1")
    except Exception as e:
        print(f"[錯誤] 請求失敗: {type(e).__name__}: {e}")
        sys.exit(1)
    print("原始回應內容:")
    print(raw2)
    try:
        parsed2 = json.loads(raw2)
        print("解析後(縮排格式):")
        print(json.dumps(parsed2, ensure_ascii=False, indent=2))
        if parsed2.get("MessageList") or parsed2.get("ChatMessageList"):
            print("\n[結果] ChannelType改成1之後讀到訊息了！代表getNewestMessageList讀P2P對話"
                  "也要用ChannelType=1，不能沿用sendChatMessage那邊P2P=0的規則。")
        else:
            print("\n[結果] ChannelType改成1還是讀不到，這個懷疑方向也被排除了。")
    except json.JSONDecodeError:
        print("[警告] 回應不是合法JSON")

    print("\n=== 診斷完成，請把上面全部輸出內容(尤其是步驟2、步驟4的原始JSON)截圖/複製傳回 ===")


if __name__ == "__main__":
    main()
