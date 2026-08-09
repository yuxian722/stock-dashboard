"""手動驗證用小工具：單次跑一下 cpis_api，印出登入/查詢結果。

在能連到 CPIS 內網、且已經設定好 config.txt 的機器上執行：

    python _probe_entity_status.py ee 2026-08-01 2026-08-09
    python _probe_entity_status.py util 2026-08-01 2026-08-09

用來確認 cpis_api.py 能不能正確登入、抓到表格資料，沒問題後再接回 run_pipeline.py。
"""

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

import cpis_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _print_rows(rows: list[list[str]], limit: int = 5) -> None:
    print(f"共 {len(rows)} 列")
    for row in rows[:limit]:
        print(row)
    if len(rows) > limit:
        print(f"...（僅顯示前 {limit} 列）")


def probe_ee(start_date: str, end_date: str) -> None:
    print("=== 登入 APG（EE Maintenance）===")
    opener = cpis_api.login()
    print("登入成功")

    print(f"=== 查詢 EE Maintenance Record {start_date} ~ {end_date} ===")
    rows = cpis_api.fetch_ee_maintenance(start_date, end_date, opener=opener)
    _print_rows(rows)


def probe_util(start_date: str, end_date: str) -> None:
    print(f"=== 查詢 Utilization Analysis {start_date} ~ {end_date} ===")
    rows = cpis_api.fetch_utilization(start_date, end_date)
    _print_rows(rows)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("ee", "util"):
        print(f"用法: python {sys.argv[0]} ee|util [start_date] [end_date]")
        sys.exit(1)

    mode = sys.argv[1]
    end_date = sys.argv[3] if len(sys.argv) > 3 else date.today().isoformat()
    start_date = sys.argv[2] if len(sys.argv) > 2 else (date.today() - timedelta(days=1)).isoformat()

    if mode == "ee":
        probe_ee(start_date, end_date)
    else:
        probe_util(start_date, end_date)


if __name__ == "__main__":
    main()
