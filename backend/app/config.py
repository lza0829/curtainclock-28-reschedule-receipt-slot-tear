from __future__ import annotations

import os
from pathlib import Path

PROJECT_NAME = "幕窗钟"

# 允许用环境变量覆盖数据库位置，便于并发自检脚本使用独立库。
_DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "curtainclock.db"
DB_PATH = Path(os.environ.get("CURTAINCLOCK_DB", str(_DEFAULT_DB))).resolve()
