"""SQLite 存取層：ee_maintenance_record、utilization_analysis 兩張表。

原始頁面欄位會隨站別/版型變動，這裡把每一列原樣存成 JSON（columns_json），
用查詢條件 + 內容算出的 hash 當唯一鍵防止重複寫入，之後要建欄位對應的清洗/
彙整邏輯可以在這之上再做一層。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "DA_BOT_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "da_bot.db")
)


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_ee_maintenance_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ee_maintenance_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            row_hash TEXT UNIQUE NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            ee_entity TEXT NOT NULL,
            columns_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def init_utilization_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS utilization_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            row_hash TEXT UNIQUE NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            util_oper TEXT NOT NULL,
            columns_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _row_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def upsert_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: list[list[str]],
    start_date: str,
    end_date: str,
    tag_column: str,
    tag_value: str,
) -> int:
    """把 rows 寫進指定表，回傳實際新增（非重複）的筆數。"""
    if table not in ("ee_maintenance_record", "utilization_analysis"):
        raise ValueError(f"未知的資料表: {table}")

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for row in rows:
        columns_json = json.dumps(row, ensure_ascii=False)
        row_hash = _row_hash(table, start_date, end_date, tag_value, columns_json)
        cur = conn.execute(
            f"""
            INSERT INTO {table} (row_hash, start_date, end_date, {tag_column}, columns_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_hash) DO NOTHING
            """,
            (row_hash, start_date, end_date, tag_value, columns_json, now),
        )
        inserted += max(cur.rowcount, 0)
    conn.commit()
    return inserted
