"""
CPIS「PM/REPAIR/SETUP Monitor」頁面診斷工具(一次性使用，確認能不能直接GET到資料)

用途：使用者提供的網址帶了UserName=EQS01這種看起來像共用顯示帳號的query
string，懷疑這頁不需要像EE Maintenance/Utilization那樣先登入，這裡直接
發一次GET，把結果印出來確認：
  1. 有沒有被導去登入頁(is_auth_fail)
  2. 有沒有抓到<table>，抓到的話印前幾列驗證欄位對不對得起來

用法: python diagnose_pm_monitor.py
"""
import cpis_api

# 使用者截圖裡完整網址列複製出來的
PM_MONITOR_URL = (
    "http://tncpisapg.tn.chipmos.com.tw/APG/APGPROD/EQUIPMENT/"
    "wFrmPMRepairSetupMonitor/Default.aspx"
    "?isCopy=True&FuncId=57&ServerName=CPIS&UserName=EQS01"
)


def main():
    print("=" * 60)
    print("GET:", PM_MONITOR_URL)
    print("=" * 60)

    opener = cpis_api.build_opener()
    html, final_url = cpis_api._read(opener, PM_MONITOR_URL, timeout=30)

    print(f"[最終網址] {final_url}")
    print(f"[HTML長度] {len(html)} 字元")

    if cpis_api.is_auth_fail(html, final_url):
        print("[結果] 被導向登入/逾時頁，這個網址不能匿名直接GET，需要先登入拿cookie")
        print("[前2000字內容]")
        print(html[:2000])
        return

    print("[結果] 沒有被導向登入頁，看起來是可以直接GET到資料的")

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("[提示] 沒裝bs4，改用陽春字串統計<table>/<tr>數量")
        print("<table>數量:", html.count("<table"))
        print("<tr>數量:", html.count("<tr"))
        print("[前3000字內容，找不到bs4沒辦法结構化解析]")
        print(html[:3000])
        return

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    print(f"[找到 {len(tables)} 個 <table>]")

    for i, table in enumerate(tables):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        print(f"\n--- table[{i}]: {len(rows)} 列 ---")
        for row in rows[:6]:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if any(cells):
                print(cells)


if __name__ == "__main__":
    main()
