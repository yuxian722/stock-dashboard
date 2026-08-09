"""
team+ 讀訊息API 診斷工具(一次性使用，不影響正式的da_bot_service.py)

用途：直接呼叫team+的ChatMainHandler.ashx，把「完整、未經任何解析」的原始JSON
印出來，讓我們直接看team+這支API實際回傳的欄位長什麼樣子，不用再靠猜的。

用法: python diagnose_teamplus.py
（跟teamplus_cookie.txt放在同一個da_bot資料夾下執行）
目前已知：NewestBatchID傳空字串會被team+的API直接拒絕(參數錯誤)，這支
腳本會多測一種寫法：NewestBatchID帶一個隨機產生的UUID(格式模仿send訊息
時自己產生的batchID)，看這樣能不能正常要到「目前最新」的訊息清單/cursor。
"""
import json
import uuid

import teamplus_api
import urllib.request
import urllib.parse


def call_read(newest_batch_id):
    cookie = teamplus_api.load_cookie()
    body = urllib.parse.urlencode({
        "action": "getNewestMessageList",
        "ChannelType": teamplus_api.CHANNEL_TYPE,
        "Mobile": teamplus_api.MOBILE,
        "ChatID": teamplus_api.CHAT_ID,
        "NewestBatchID": newest_batch_id,
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
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def show(label, data):
    print("=" * 60)
    print(label)
    print("=" * 60)
    print("[最上層的所有欄位名稱]", list(data.keys()))
    for key in ("ChatMessageList", "MessageList"):
        val = data.get(key)
        print(f"[{key}] {'不存在' if val is None else f'{len(val)} 筆'}")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print()


print("ChatID:", teamplus_api.CHAT_ID)
print()

print("--- 測試1: NewestBatchID = 空字串(目前程式原本的寫法) ---")
try:
    data1 = call_read("")
    show("測試1結果", data1)
except Exception as e:
    print(f"[錯誤] {type(e).__name__}: {e}\n")

print("--- 測試2: NewestBatchID = 隨機UUID(模仿送訊息時的batchID格式) ---")
try:
    fake_bid = str(uuid.uuid4())
    print("用的UUID:", fake_bid)
    data2 = call_read(fake_bid)
    show("測試2結果", data2)
except Exception as e:
    print(f"[錯誤] {type(e).__name__}: {e}\n")

print("把上面測試1、測試2的完整結果都貼給Claude看")
