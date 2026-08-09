"""Utilization Analysis 爬蟲。

改用 cpis_api（純 requests/urllib）取代 Selenium 附身模式。抓取／表格解析
（多表格合併、rowspan 展開、frame 遞迴掃描、session 過期時的 btnReLogon 續命）都在
cpis_api.fetch_utilization() 裡處理；這裡只做「抓到 rows 之後」的資料清洗：

    1. 表頭正規化（去除全形空白、多餘空白）
    2. 過濾 SUM/TARGET/合計/小計 這類彙總列
    3. session 過期時（CpisAuthError）重試整個查詢
    4. 欄位白名單過濾（目前先留空 = 不過濾，等確認 CPIS 實際欄位名稱後再填）
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

import cpis_api
import db

logger = logging.getLogger(__name__)

_SUMMARY_KEYWORDS = ("SUM", "TARGET", "TOTAL", "合計", "小計", "目標")

# 已知/預期出現的欄位名稱（正規化後）。目前先留空 = 不做欄位白名單過濾，只保留全部欄位；
# 之後確認 CPIS 實際欄位名稱後，把要保留的欄位填進來即可自動過濾未預期的新增欄位。
WHITELIST_COLUMNS: tuple[str, ...] | None = None


def _split_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _normalize_header(cell: str) -> str:
    cell = cell.replace("　", " ")  # 全形空白
    return re.sub(r"\s+", " ", cell).strip()


def _is_summary_row(row: list[str]) -> bool:
    for cell in row:
        cell = cell.strip()
        if not cell:
            continue
        return any(kw in cell.upper() for kw in _SUMMARY_KEYWORDS)
    return False


def _apply_whitelist(
    header: list[str], rows: list[list[str]]
) -> tuple[list[str], list[list[str]]]:
    if not WHITELIST_COLUMNS:
        return header, rows
    keep_idx = [i for i, name in enumerate(header) if name in WHITELIST_COLUMNS]
    if not keep_idx:
        logger.warning("Utilization: 白名單沒有任何欄位對得上目前的表頭 %s，略過過濾", header)
        return header, rows
    new_header = [header[i] for i in keep_idx]
    new_rows = [[row[i] if i < len(row) else "" for i in keep_idx] for row in rows]
    return new_header, new_rows


def _fetch_with_retry(start_date: str, end_date: str, util_oper: str, retries: int = 2) -> list[list[str]]:
    last_err: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            return cpis_api.fetch_utilization(start_date, end_date, util_oper=util_oper)
        except cpis_api.CpisAuthError as e:
            last_err = e
            logger.warning("Utilization: 第 %d 次查詢驗證失敗（%s），重試登入", attempt, e)
    assert last_err is not None
    raise last_err


def run(start_date: str, end_date: str, util_oper: str = "DA") -> int:
    """抓取 Utilization Analysis 並寫入 SQLite，回傳新增的筆數。"""
    rows = _fetch_with_retry(start_date, end_date, util_oper)
    logger.info("Utilization: fetched %d raw rows (%s ~ %s)", len(rows), start_date, end_date)

    header, data_rows = _split_header(rows)
    header = [_normalize_header(c) for c in header]
    data_rows = [r for r in data_rows if not _is_summary_row(r)]
    header, data_rows = _apply_whitelist(header, data_rows)
    logger.info("Utilization: header=%s, %d rows after cleaning", header, len(data_rows))

    conn = db.get_connection()
    try:
        db.init_utilization_table(conn)
        inserted = db.upsert_rows(
            conn, "utilization_analysis", data_rows, start_date, end_date, "util_oper", util_oper
        )
    finally:
        conn.close()

    logger.info("Utilization: inserted %d new rows", inserted)
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    today = date.today()
    yesterday = today - timedelta(days=1)
    run(yesterday.isoformat(), today.isoformat())
