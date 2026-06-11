#!/usr/bin/env python3
"""
SimController 入口脚本

启动风淋门/区域管控 模拟器，端口 5112。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sim_controller.main import main

if __name__ == "__main__":
    main()
