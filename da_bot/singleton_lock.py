"""
單一執行個體保護：確保team+即時問答監聽(teamplus_listener.py/da_bot_service.py)
同時間只有一個process在跑。

背景：2026/08/10使用者回報看到"DB改機"訊息無限自問自答的迴圈(終端機log
顯示同一句"DB改機 今日目前沒有完成的改機紀錄"被當成新指令、一直重複回覆，
直到60秒8次的洗版保護才讓那個process安靜下來，但立刻又開始下一輪)。

追查最可能的成因：teamplus_listener.py/da_bot_service.py的自問自答保護
(sent_batch_ids)只存在單一process的記憶體裡，process之間互不知道對方
送出過什麼訊息。如果背景排程的da_bot_service.py跟手動另外開的
teamplus_listener.py(或另一份da_bot_service.py)同時在跑，會變成：
process A送出的回覆，process B(不知道這是process A自己送的)會當成新指令
再回一次；process A又不知道那是process B送的，也當成新指令再回一次——
兩個process的回覆本身又都剛好含有"DB改機"這個觸發關鍵字，於是無限循環
下去，只是各自的60秒洗版保護獨立計算，兩個process加起來的總回覆數不受
限制，訊息量比單一process的保護上限預期的還要多很多。

用msvcrt(Windows)/fcntl(其他平台)鎖住一個固定檔案，拿不到鎖就代表已經有
別的process在跑監聽，直接印出錯誤訊息結束，不要跟現有的process搶著回覆
同一個聊天室。作業系統會在process結束(包含當機、被工作管理員強制關閉)時
自動釋放鎖定，不會有鎖定檔案忘記解鎖、卡死後面啟動的問題。
"""
import os
import sys

_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teamplus_listener.lock")

# 保留鎖定檔案物件的參照，process存活期間不能被GC關閉，不然鎖會提早釋放
_lock_file = None


def acquire_or_exit():
    """
    試著取得監聽用的單一執行個體鎖，拿不到就印出錯誤訊息並結束process
    (sys.exit(1))，避免兩個process同時監聽同一個team+聊天室、彼此把對方
    的回覆當成新指令、無限自問自答。teamplus_listener.py的main()跟
    da_bot_service.py的main()都要在開始監聽前呼叫這個函式。
    """
    global _lock_file
    try:
        f = open(_LOCK_PATH, "a+")
    except OSError as e:
        print(f"[警告] 無法開啟單一執行個體鎖定檔({_LOCK_PATH}): {e}，略過保護繼續執行")
        return

    try:
        if sys.platform.startswith("win"):
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[錯誤] 偵測到已經有另一個team+即時問答監聽process在執行中，"
              "不能同時跑兩個(兩個process會互相把對方的回覆當成新指令、"
              "無限自問自答，這就是2026/08/10發現的DB改機無限迴圈的成因)。"
              "請確認da_bot_service.py背景服務有沒有已經在跑(工作管理員搜尋"
              "python)，不要再手動另外開一個teamplus_listener.py/"
              "da_bot_service.py，這支process即將結束。")
        f.close()
        sys.exit(1)

    _lock_file = f  # 保留參照避免被關閉，process結束時作業系統自動解鎖
