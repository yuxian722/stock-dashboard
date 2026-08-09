"""
team+ 讀訊息API 診斷工具(一次性使用，不影響正式的da_bot_service.py)

用途：直接呼叫team+的ChatMainHandler.ashx，把「完整、未經任何解析」的原始JSON
印出來，讓我們直接看team+這支API實際回傳的欄位長什麼樣子，不用再靠猜的。

用法: python diagnose_teamplus.py
（跟teamplus_cookie.txt放在同一個da_bot資料夾下執行）
"""
import json

import teamplus_api

print("=" * 60)
print("ChatID:", teamplus_api.CHAT_ID)
print("=" * 60)

cookie = teamplus_api.load_cookie()
print(f"[cookie] 讀到{len(cookie)}個字元\n")

import urllib.request
import urllib.parse

body = urllib.parse.urlencode({
    "action": "getNewestMessageList",
    "ChannelType": teamplus_api.CHANNEL_TYPE,
    "Mobile": teamplus_api.MOBILE,
    "ChatID": teamplus_api.CHAT_ID,
    "NewestBatchID": "",
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

try:
    with urllib.request.urlopen(req, context=teamplus_api._SSL_CTX, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
except Exception as e:
    print(f"[錯誤] 連線失敗: {type(e).__name__}: {e}")
    raise SystemExit(1)

print("[原始JSON長度]", len(raw), "字元\n")

try:
    data = json.loads(raw)
except Exception as e:
    print(f"[錯誤] 不是合法JSON: {e}")
    print("[原始內容前2000字]")
    print(raw[:2000])
    raise SystemExit(1)

print("[最上層的所有欄位名稱]")
print(list(data.keys()))
print()

for key in ("ChatMessageList", "MessageList"):
    val = data.get(key)
    print(f"[{key}] {'不存在' if val is None else f'{len(val)} 筆'}")

print()
print("=" * 60)
print("完整JSON內容(把這整段貼給Claude看)：")
print("=" * 60)
print(json.dumps(data, ensure_ascii=False, indent=2))
