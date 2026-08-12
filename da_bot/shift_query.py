# -*- coding: utf-8 -*-
"""
班別(AD/AN/BD/BN)改機查詢(2026/08/10使用者要求)。

背景：CPIS EE Maintenance Record報表的「Shift」是查詢時的過濾參數
(cpis_api._ee_query_string()裡的shift=)，不是回傳資料裡的一個欄位——
同一段時間查shift=None會拿到AD+AN+BD+BN全部班別的紀錄合在一起，事後
沒辦法從資料本身分辨出處。使用者確認：A/B是班組、D/N是早班/夜班。

使用者明確要求不要把這個功能做進整點自動排程(run_pipeline.py)裡多抓
4次(AD/AN/BD/BN各一次)——那樣會讓每次整點任務的執行時間拉長、也會讓
CPIS的請求量變成4倍。改成「查詢的當下才即時去CPIS抓」，只在真的有人
在team+問班別問題時才發生，不進正式ETL流程、也不寫進本地SQLite——
每次查完就丟掉，不快取。

即時查CPIS要重新登入+下載+解析報表，實測其他報表(cpis_scraper.py)
需要到幾十秒，呼叫端(teamplus_listener.py)一定要把這個函式丟到背景
執行緒執行，不能卡住即時問答的主迴圈(同一個理由，比照da_bot_service.py
把整點任務丟背景執行緒的做法)。
"""
import cpis_api
import cpis_scraper
import hourly_push
import query_bot

# CPIS查詢頁「Shift」下拉選單的值(2026/08/10使用者截圖確認)。
SHIFT_LABELS = {"AD": "A班早班", "AN": "A班夜班", "BD": "B班早班", "BN": "B班夜班"}


def is_valid_shift(shift):
    return bool(shift) and shift.upper() in SHIFT_LABELS


def live_group_shift_changeover_reply(group_name: str, shift: str, date_ymd: str, date_label: str) -> str:
    """
    即時向CPIS查詢指定班別(AD/AN/BD/BN)+指定日期+指定機型群組的改機明細，
    不經過本地資料庫、每次都是即時抓即時算，格式跟query_bot.
    group_changeover_detail_reply()共用query_bot._changeover_report_text()，
    看起來會是同一種報表，只是資料來源是「這次即時查到的」而不是「本地DB
    裡已經抓好的」。

    date_ymd格式YYYYMMDD(單一天，日期範圍就是這一天到這一天)；date_label
    是訊息裡顯示用的日期字樣(例如"08/11")。這支函式本身不處理例外——CPIS
    連線失敗/沒資料的情況都要能正常回傳文字說明，不能讓呼叫端(背景執行緒)
    直接死掉，所以内部包了try/except。
    """
    shift = shift.upper()
    shift_word = SHIFT_LABELS.get(shift, shift)
    display_name = query_bot._CHANGEOVER_GROUP_DISPLAY.get(group_name, group_name)
    day_word = f"{date_label}（{shift_word}）"

    try:
        xls_chunks = cpis_api.fetch_ee_maintenance_xls(date_ymd, date_ymd, entity="BA*", shift=shift)
    except Exception as e:
        return f"{display_name}改機（{shift_word}）{date_label} 即時查詢CPIS失敗: {type(e).__name__}: {e}"

    records = []
    for raw in xls_chunks:
        records.extend(cpis_scraper.parse_ee_maintenance_xls(raw))

    changeover_records = [r for r in records if r.get("e_tag") == "S"]
    rows = query_bot._filter_changeover_rows(changeover_records, group_name)

    return query_bot._changeover_report_text(display_name, day_word, rows)
