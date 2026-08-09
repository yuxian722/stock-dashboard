"""da_bot 背景服務進入點：定時呼叫 run_pipeline。

CPIS 已改用 requests/urllib 實作（見 cpis_api.py），這裡移除了原本殘留的、啟動時
檢查/啟動除錯模式 Edge 的邏輯——不再需要瀏覽器。
"""

from __future__ import annotations

import logging
import time

import run_pipeline

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 60 * 60  # 預設每小時跑一次


def main(interval_seconds: int = INTERVAL_SECONDS) -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("da_bot 服務啟動，每 %d 秒執行一次 pipeline", interval_seconds)
    while True:
        try:
            run_pipeline.run()
        except Exception:
            logger.exception("run_pipeline 執行失敗")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
