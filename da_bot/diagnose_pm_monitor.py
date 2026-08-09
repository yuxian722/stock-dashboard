"""
CPIS「PM/REPAIR/SETUP Monitor」頁面診斷工具(一次性使用，確認能不能直接GET到資料)

第一輪測試發現：直接GET這個網址不會被導向登入頁(不用登入)，但抓到的<table>
內容是「公佈欄異常處理」那些欄位，不是畫面上看到的OPER/ENTITY/MODEL/STATUS
機況表格——研判跟Utilization Analysis一樣，真正的資料表其實在裡面的
iframe(子頁面)，不是同一份HTML。這裡改用cpis_api.py既有的iframe遞迴掃描
邏輯(_collect_html_recursive，跟抓Utilization Analysis用的是同一套)，
把主頁面裡所有iframe/frame也一併抓下來，逐一列出每一層抓到的<table>，
確認哪一層才是真正的機況表格。

用法: python diagnose_pm_monitor.py
"""
import cpis_api

# 使用者截圖裡完整網址列複製出來的
PM_MONITOR_URL = (
    "http://tncpisapg.tn.chipmos.com.tw/APG/APGPROD/EQUIPMENT/"
    "wFrmPMRepairSetupMonitor/Default.aspx"
    "?isCopy=True&FuncId=57&ServerName=CPIS&UserName=EQS01"
)

# 在瀏覽器F12主控台對mainFrame打location.href找到的實際網址(真正顯示機況
# 表格的那個frame)，沒有帶任何querystring參數，猜測是靠session/cookie記住
# Oper Kind選項(D/A)，不是靠網址參數決定
MAIN_FRAME_URL = (
    "http://tncpisapg.tn.chipmos.com.tw/APG/APGPROD/EQUIPMENT/"
    "wFrmPMRepairSetupMonitor/ent_st11.aspx"
)


def _print_tables(html, label):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print(f"[{label}] 沒裝bs4，改用陽春字串統計<table>/<tr>數量")
        print(f"[{label}] <table>數量:", html.count("<table"))
        return

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    print(f"\n{'=' * 60}\n[{label}] HTML長度={len(html)}字元，找到{len(tables)}個<table>\n{'=' * 60}")

    hit_target = False
    for i, table in enumerate(tables):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        header_text = " ".join(c.get_text(strip=True) for c in rows[0].find_all(["td", "th"]))
        is_target = ("ENTITY" in header_text.upper() and "STATUS" in header_text.upper())
        if is_target:
            hit_target = True
        marker = "  <== 看起來就是這個！(表頭含ENTITY+STATUS)" if is_target else ""
        print(f"\n--- table[{i}]: {len(rows)} 列{marker} ---")
        for row in rows[:6]:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if any(cells):
                print(cells)

    if hit_target:
        print(f"\n[{label}] *** 找到目標表格，就是這一層！把上面完整輸出貼給Claude即可 ***")


def main():
    print("=" * 60)
    print("GET:", PM_MONITOR_URL)
    print("=" * 60)

    opener = cpis_api.build_opener()
    html, final_url = cpis_api._read(opener, PM_MONITOR_URL, timeout=30)

    print(f"[最終網址] {final_url}")

    if cpis_api.is_auth_fail(html, final_url):
        print("[結果] 被導向登入/逾時頁，這個網址不能匿名直接GET，需要先登入拿cookie")
        print("[前2000字內容]")
        print(html[:2000])
        return

    print("[結果] 沒有被導向登入頁，看起來是可以直接GET到資料的")

    iframe_srcs = cpis_api._iframe_srcs(html)
    print(f"[主頁面裡的iframe/frame數量] {len(iframe_srcs)}")
    for src in iframe_srcs:
        print("  -", src)

    _print_tables(html, "主頁面")

    all_htmls = cpis_api._collect_html_recursive(opener, html, final_url, cpis_api.MAX_FRAME_DEPTH)
    for i, frame_html in enumerate(all_htmls[1:], start=1):  # [0]就是主頁面，已經印過了
        _print_tables(frame_html, f"iframe第{i}層")

    # 額外測試：在瀏覽器F12主控台對mainFrame打location.href找到的實際網址，
    # 沿用同一個opener(帶著剛才GET Default.aspx拿到的cookie)、以Default.aspx
    # 當Referer，直接GET看看能不能拿到跟畫面上一樣的機況表格
    print("\n" + "#" * 60)
    print("額外測試：直接GET mainFrame的實際網址(帶著同一組cookie)")
    print("GET:", MAIN_FRAME_URL)
    print("#" * 60)

    import urllib.request
    req = urllib.request.Request(MAIN_FRAME_URL)
    req.add_header("Referer", final_url)
    main_html, main_final_url = cpis_api._read(opener, req, timeout=30)
    print(f"[最終網址] {main_final_url}")

    if cpis_api.is_auth_fail(main_html, main_final_url):
        print("[結果] 被導向登入/逾時頁，這條路線需要更完整的session/cookie才能用")
    else:
        _print_tables(main_html, "mainFrame直接GET")


if __name__ == "__main__":
    main()
