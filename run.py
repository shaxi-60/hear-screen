"""源码运行入口：python run.py"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 源码运行时确保能找到 hear_screen 包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hear_screen.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
