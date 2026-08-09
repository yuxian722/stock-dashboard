"""da_bot 排程主流程：依序執行各爬蟲。

CPIS 相關爬蟲已改用 cpis_api 的 requests/urllib 實作，不再依賴瀏覽器，這裡不需要
（也不應該再有）任何檢查/管理除錯模式 Edge 分頁的邏輯。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import cpis_scraper
import cpis_utilization_scraper

logger = logging.getLogger(__name__)

_STEPS = (
    ("EE Maintenance Record", cpis_scraper.run),
    ("Utilization Analysis", cpis_utilization_scraper.run),
)


def run(start_date: str | None = None, end_date: str | None = None) -> None:
    if end_date is None:
        end_date = date.today().isoformat()
    if start_date is None:
        start_date = (date.today() - timedelta(days=1)).isoformat()

    for name, step in _STEPS:
        try:
            step(start_date, end_date)
        except Exception:
            logger.exception("%s 爬蟲執行失敗", name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
