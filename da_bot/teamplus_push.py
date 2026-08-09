"""
team+ 整點自動推播 (08/06改版：使用team+ HTTP API，不需要Edge/Selenium)

原本這支腳本靠附身模式Edge打字送出訊息，除錯了一整天後改用同事逆向出來的
team+後端HTTP API(teamplus_api.py)，不再需要瀏覽器自動化。

用法: python teamplus_push.py

這個腳本會呼叫 hourly_push.py 的 build_hourly_push_message() 組出訊息，
再透過teamplus_api.broadcast_message()送出。預設只送到CHAT_ID(機器人推播室)，
要多推播到其他聊天室的話，把該室的ChatID加進config.txt的
teamplus_extra_chat_ids(逗號分隔)即可，不用改這支腳本。
"""
import sys

# Windows主控台預設用cp950(繁體中文)編碼，推播訊息裡的emoji(🔧⏳⚡等)沒辦法
# 用cp950編碼，print()會直接丟UnicodeEncodeError把整支腳本弄當掉，導致
# 推播失敗。改成把stdout/stderr強制用utf-8輸出(比照同事teamplus_bot.py的
# 做法)，encode不了的字元用errors="replace"跳過，不會再整支當掉。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import teamplus_api
from hourly_push import build_hourly_push_message


if __name__ == "__main__":
    msg = build_hourly_push_message()
    print("[準備推播的訊息內容]")
    print(msg)
    print()

    results = teamplus_api.broadcast_message(msg)
    all_ok = True
    for chat_id, ok, desc in results:
        status = "[完成]" if ok else "[失敗]"
        print(f"{status} [{chat_id}] {desc}")
        all_ok = all_ok and ok

    if not all_ok:
        sys.exit(1)
