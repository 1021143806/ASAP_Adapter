#!/usr/bin/env python3
"""
ASAP Adapter - 风淋门-区域管控协议适配器

入口脚本，用于 Supervisor 直接调用。
"""

import sys
from pathlib import Path

# 确保项目目录在 sys.path 中
project_dir = Path(__file__).parent
sys.path.insert(0, str(project_dir))

from asap_adapter.main import main

if __name__ == "__main__":
    main()
