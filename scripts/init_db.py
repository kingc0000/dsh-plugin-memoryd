#!/usr/bin/env python3
"""初始化数据库（建库建表）。幂等。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402
from app.config import settings  # noqa: E402

if __name__ == "__main__":
    init_db()
    print(f"✓ 数据库就绪: {settings.db_path}")