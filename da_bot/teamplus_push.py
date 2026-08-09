"""
team+ 整點自動推播 (08/06改版：使用team+ HTTP API，不需要Edge/Selenium)

原本這支腳本靠附身模式Edge打字送出訊息，除錯了一整天後改用同事逆向出來的
team+後端HTTP API(teamplus_api.py)，不再需要瀏覽器自動化。

用法: python teamplus_push.py

這個腳本會呼叫 hourly_push.py 的 build_hourly_push_message() 組出訊息，
再透過teamplus_api.send_message()送出，邏輯跟舊版一致，只是送出方式改變。
"""
import sys

import teamplus_api
from hourly_push import build_hourly_push_message


if __name__ == "__main__":
    msg = build_hourly_push_message()
    print("[準備推播的訊息內容]")
    print(msg)
    print()

    ok, desc = teamplus_api.send_message(msg)
    if ok:
        print(f"[完成] {desc}")
    else:
        print(f"[失敗] {desc}")
        sys.exit(1)
