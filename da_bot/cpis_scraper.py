"""EE Maintenance Record 爬蟲。

改用 cpis_api（純 requests/urllib）取代 Selenium 附身模式，不再需要開啟已登入的
Edge 分頁，也不受除錯模式 Edge 開不起來的環境問題影響。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import cpis_api
import db

logger = logging.getLogger(__name__)


def run(
    start_date: str,
    end_date: str,
    ee_entity: str = "DA",
    ee_etag: str = "None(P,R,S,QC)",
) -> int:
    """抓取 EE Maintenance Record 並寫入 SQLite，回傳新增的筆數。"""
    rows = cpis_api.fetch_ee_maintenance(start_date, end_date, ee_entity=ee_entity, ee_etag=ee_etag)
    logger.info("EE Maintenance: fetched %d rows (%s ~ %s)", len(rows), start_date, end_date)

    conn = db.get_connection()
    try:
        db.init_ee_maintenance_table(conn)
        inserted = db.upsert_rows(
            conn, "ee_maintenance_record", rows, start_date, end_date, "ee_entity", ee_entity
        )
    finally:
        conn.close()

    logger.info("EE Maintenance: inserted %d new rows", inserted)
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    today = date.today()
    yesterday = today - timedelta(days=1)
    run(yesterday.isoformat(), today.isoformat())
