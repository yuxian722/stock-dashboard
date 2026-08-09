"""讀取 da_bot 的設定檔（key=value 格式）。

CPIS/APG 帳密統一放這裡讀取，不寫死在程式碼裡。設定檔預設路徑是跟本檔同層的
``config.txt``（複製 ``config.txt.example`` 改名並填入實際帳密），也可用環境變數
``DA_BOT_CONFIG`` 指到別的路徑。
"""

from __future__ import annotations

import os

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")

_REQUIRED_KEYS = ("apg_user", "apg_password", "util_user", "util_password")


def _config_path() -> str:
    return os.environ.get("DA_BOT_CONFIG", _DEFAULT_PATH)


def load(path: str | None = None) -> dict:
    """解析 key=value 設定檔，`#` 開頭當註解、空白行略過。"""
    path = path or _config_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"找不到設定檔 {path}，請複製 config.txt.example 改名為 config.txt 並填入帳密"
        )

    cfg: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            cfg[key.strip()] = value.strip()
    return cfg


def require(cfg: dict, *keys: str) -> None:
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        raise KeyError(f"config.txt 缺少必要欄位: {', '.join(missing)}")
