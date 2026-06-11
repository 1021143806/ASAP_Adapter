"""
日志配置模块

支持按大小轮转的日志文件 + 控制台输出
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LogConfig


def setup_logging(config: LogConfig):
    """配置日志系统"""
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, config.level.upper(), logging.INFO))

    # 格式
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 文件输出（按大小轮转）
    log_path = Path(config.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=_parse_size(config.rotation),
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def _parse_size(size_str: str) -> int:
    """解析大小字符串，如 '5 MB' → 5242880"""
    size_str = size_str.strip().upper()
    if size_str.endswith("KB"):
        return int(float(size_str[:-2].strip()) * 1024)
    elif size_str.endswith("MB"):
        return int(float(size_str[:-2].strip()) * 1024 * 1024)
    elif size_str.endswith("GB"):
        return int(float(size_str[:-2].strip()) * 1024 * 1024 * 1024)
    else:
        return int(size_str)
