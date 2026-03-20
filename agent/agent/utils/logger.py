#!/usr/bin/env python3
"""
统一日志模块：同时输出到控制台和日志文件。

用法：
    from utils.logger import get_logger
    logger = get_logger("module_name")
    logger.info("消息")
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from config import LOG_DIR, LOG_LEVEL

# 确保日志目录存在
_log_dir = Path(__file__).resolve().parent.parent / LOG_DIR
_log_dir.mkdir(parents=True, exist_ok=True)

# 日志文件名：按天分割
_log_file = _log_dir / f"agent_{datetime.now().strftime('%Y-%m-%d')}.log"

# 日志格式
_FORMAT = "[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 全局只配置一次 root handler
_initialized = False


def _init_root():
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.DEBUG))

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # 控制台 handler
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件 handler（UTF-8，追加模式）
    file_handler = logging.FileHandler(_log_file, encoding="utf-8", mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 压制第三方库的 DEBUG 日志
    for noisy in ("httpcore", "httpx", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取带模块名的 logger。"""
    _init_root()
    return logging.getLogger(name)
