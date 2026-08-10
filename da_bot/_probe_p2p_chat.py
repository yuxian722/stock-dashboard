# -*- coding: utf-8 -*-
"""手動驗證用小工具：直接呼叫我們自己的程式碼實際發送給team+ API的HTTP
請求，印出完整的原始回應，診斷1對1個人對話(P2P，例如"APG_DA班長"，
ChatID"903_1631")讀不到訊息的問題。

第一版(v1)先驗證了「懷疑getNewestMessageList要用getOneOnOneChatInfo
回傳的『真正』ChatID，而不是送訊息用的903_1631格式」這個假設——結果
getOneOnOneChatInfo回傳的ChatID也是"903_1631"，跟現在用的一樣，
表示ChatID格式不是問題根源。

這一版(v2)換個角度：v1的步驟2故意用「隨機UUID」當NewestBatchID，這其實
跟之前額外聊天室「靜默bootstrap」被證實不可靠的那個模式一模一樣(cursor
沒有對應到任何真實訊息，team+的API判斷不出「這之後有沒有新訊息」)，
用隨機UUID讀到"查無資料"不能證明什麼。這一版改成完全比照
teamplus_listener.py實際的運作方式：

  1. 送一則真正的訊息到這個聊天室，拿到它「真實」的batchID當cursor
     (這就是_init_room_state()開機時做的事)
  2. 請你實際在team+那個聊天室裡輸入任何文字(模擬你平常打「查詢」的
     操作)
  3. 用步驟1拿到的真實cursor呼叫getNewestMessageList(這就是
     _poll_room_once()每一輪在做的事)，看能不能讀到你剛剛打的那則訊息

如果步驟3還是讀不到，就能排除「cursor bootstrap方式不對」，問題應該在
更底層(例如team+對P2P對話的getNewestMessageList本來就有其他限制)。

用法：
    python _probe_p2p_chat.py 903_1631
"""
import sys
import json

import teamplus_api


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

    print("\n===== 步驟2: 用剛剛拿到的真實cursor呼叫read_new_messages() =====")
    print(f"(這一步跟teamplus_listener.py的_poll_room_once()做的事完全一樣，cursor={bid!r})")
    messages, new_cursor = teamplus_api.read_new_messages(bid, chat_id=chat_id)
    print(f"messages = {json.dumps(messages, ensure_ascii=False, indent=2)}")
    print(f"new_cursor = {new_cursor!r}")

    if messages:
        print("\n[結果] 有讀到訊息！代表cursor/ChatID都沒問題，"
              "如果之前服務仍然沒反應，問題可能在別的地方(例如洗版保護、"
              "parse_query()判斷邏輯，而不是team+ API本身)")
    else:
        print("\n[結果] 還是沒讀到任何訊息。用的已經是『真實』cursor("
              "不是隨機UUID)，如果你剛剛確實有在那個聊天室打字，"
              "這就證明team+的getNewestMessageList對這個P2P聊天室"
              "本身有問題(不是我們cursor bootstrap方式的問題)。")

    print("\n=== 診斷完成，請把上面全部輸出內容截圖/複製傳回 ===")


if __name__ == "__main__":
    main()
