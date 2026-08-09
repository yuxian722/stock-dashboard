"""手動驗證用小工具：單次跑一下 cpis_api，印出登入/查詢結果，不寫入資料庫。

在能連到 CPIS 內網、且已經設定好 config.txt 的機器上執行：

    python _probe_entity_status.py ee 20260801 20260809 BA*
    python _probe_entity_status.py util 20260809 20260809

用來確認 cpis_api.py 能不能正確登入、抓到表格資料，沒問題後再接回
cpis_scraper.py / cpis_utilization_scraper.py 的正式流程(run_pipeline.py)。
"""
import sys
import datetime

import cpis_api
import cpis_scraper
import cpis_utilization_scraper


def _print_records(records, limit=5):
    print(f"共 {len(records)} 筆")
    for r in records[:limit]:
        print(r)
    if len(records) > limit:
        print(f"...(僅顯示前{limit}筆)")


def probe_ee(date_start, date_end, entity_pattern):
    print(f"=== 查詢EE Maintenance Record {date_start} ~ {date_end}, Entity={entity_pattern} ===")
    xls_chunks = cpis_api.fetch_ee_maintenance_xls(date_start, date_end, entity_pattern)
    print(f"共下載 {len(xls_chunks)} 份報表")
    records = []
    for raw in xls_chunks:
        records.extend(cpis_scraper.parse_ee_maintenance_xls(raw))
    _print_records(records)


def probe_util(date_start, date_end):
    print(f"=== 查詢Utilization Analysis {date_start} ~ {date_end} ===")
    tables = cpis_utilization_scraper.fetch_tables(date_start, date_end)
    print(f"共收集到 {len(tables)} 個<table>")
    cpis_utilization_scraper.print_table_diagnostics(tables)
    records = cpis_utilization_scraper.parse_tables(tables)
    _print_records(records)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("ee", "util"):
        print(f"用法: python {sys.argv[0]} ee|util [date_start YYYYMMDD] [date_end YYYYMMDD] [entity_pattern]")
        sys.exit(1)

    mode = sys.argv[1]
    if len(sys.argv) >= 4:
        date_start, date_end = sys.argv[2], sys.argv[3]
    else:
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        date_start = yesterday.strftime("%Y%m%d")
        date_end = today.strftime("%Y%m%d")

    try:
        if mode == "ee":
            entity_pattern = sys.argv[4] if len(sys.argv) > 4 else "BA*"
            probe_ee(date_start, date_end, entity_pattern)
        else:
            probe_util(date_start, date_end)
    except (cpis_api.CpisAuthError, ValueError) as e:
        print(f"[錯誤] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
