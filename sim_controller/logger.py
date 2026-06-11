"""
模拟器日志配置
"""

import logging
import sys


def setup_logging(level: str = "DEBUG"):
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, level, logging.DEBUG))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(handler)
