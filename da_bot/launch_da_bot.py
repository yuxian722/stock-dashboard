"""
DA監控機器人 - 一鍵啟動器 (CPIS改版：整點任務也不再需要Edge)

雙擊 launch_da_bot.bat(或執行 python launch_da_bot.py)後，直接啟動
da_bot_service.py(整點推播+即時問答合併服務)。

════════════════════════════════════════
改版說明
════════════════════════════════════════
即時問答已經改用team+ HTTP API(teamplus_api.py)，整點任務的CPIS資料更新
也已經改用HTTP API(cpis_api.py)，完全不再需要附身模式Edge，這支啟動器
不用再檢查/等待除錯模式Edge就緒，也不會再卡在Edge除錯埠開不起來的問題。

前置：
- da_bot資料夾下要有 teamplus_cookie.txt(見teamplus_api.py開頭說明)
- da_bot資料夾下要有 config.txt(見config.txt.example，CPIS/APG帳密)
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    os.chdir(SCRIPT_DIR)  # 確保相對路徑(da_maintenance.db、teamplus_cookie.txt等)都對到da_bot資料夾

    if not os.path.exists(os.path.join(SCRIPT_DIR, "teamplus_cookie.txt")):
        print("[錯誤] 找不到 teamplus_cookie.txt")
        print("       請依teamplus_api.py開頭說明，用F12開發者工具抓team+的cookie存成這個檔案")
        sys.exit(1)

    if not os.path.exists(os.path.join(SCRIPT_DIR, "config.txt")):
        print("[錯誤] 找不到 config.txt")
        print("       請複製 config.txt.example 改名為 config.txt，填入CPIS/APG帳密")
        sys.exit(1)

    print("[提示] 啟動 da_bot_service.py(整點推播 + 即時問答合併服務)...")
    print("=" * 50)
    import da_bot_service
    da_bot_service.main()
