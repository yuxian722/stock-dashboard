# -*- coding: utf-8 -*-
"""
班別(AD/AN/BD/BN)改機查詢(2026/08/10使用者要求)。

背景：CPIS EE Maintenance Record有兩個查詢入口。原本用的
maintenance_record_r.aspx(「report產生端點」，cpis_api.fetch_ee_
maintenance_xls())query string雖然也接受shift參數，但2026/08/12使用者
實測發現(比對shift=None跟AD/AN/BD/BN四班查出來的筆數/內容)伺服器端根本
沒有真正套用這個篩選——shift=AD查出來的筆數跟shift=None一模一樣，等於
沒篩選。真正有實作Shift篩選的是另一個查詢表單頁面maintenance_record_h.
aspx。

這個表單一開始改用cpis_api.py純urllib模擬POST(帶__VIEWSTATE/
__EVENTVALIDATION/85項Operation複選框欄位)，但實測連續兩輪都回500
Internal Server Error(先後補齊__EVENTTARGET/__EVENTARGUMENT、再改成
先訪問帶FuncId的選單入口頁「暖身」都沒解決)，改用cpis_ee_shift_scraper.py
的無頭瀏覽器做法(跟cpis_pm_monitor_scraper.py同款、已驗證可行)：真的
打開無頭Edge，實際選好Shift/Entity/日期/E-tag、按Fetch按鈕，讓瀏覽器
自己處理所有ASP.NET postback細節。解析仍用cpis_scraper.
parse_ee_maintenance_shift_html()(BeautifulSoup解析HTML表格，跟資料
是HTTP POST回來的還是瀏覽器渲染出來的無關)。A/B是班組、D/N是早班/夜班
(使用者確認)。

使用者明確要求不要把這個功能做進整點自動排程(run_pipeline.py)裡多抓
4次(AD/AN/BD/BN各一次)——那樣會讓每次整點任務的執行時間拉長、也會讓
CPIS的請求量變成4倍。改成「查詢的當下才即時去CPIS抓」，只在真的有人
在team+問班別問題時才發生，不進正式ETL流程、也不寫進本地SQLite——
每次查完就丟掉，不快取。

即時查CPIS要開無頭瀏覽器+登入+查詢+解析頁面，實測其他Selenium報表
(cpis_pm_monitor_scraper.py)需要到幾十秒，呼叫端(teamplus_listener.py)
一定要把這個函式丟到背景執行緒執行，不能卡住即時問答的主迴圈(同一個
理由，比照da_bot_service.py把整點任務丟背景執行緒的做法)。
"""
import cpis_ee_shift_scraper
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
        result_html = cpis_ee_shift_scraper.fetch_ee_maintenance_shift_html(
            date_ymd, date_ymd, entity="BA*", shift=shift, etag="S"
        )
    except Exception as e:
        return f"{display_name}改機（{shift_word}）{date_label} 即時查詢CPIS失敗: {type(e).__name__}: {e}"

    records = cpis_scraper.parse_ee_maintenance_shift_html(result_html)
    changeover_records = [r for r in records if r.get("e_tag") == "S"]
    rows = query_bot._filter_changeover_rows(changeover_records, group_name)

    return query_bot._changeover_report_text(display_name, day_word, rows)
