#!/usr/bin/env python3
"""
Unified logging module: outputs to both console and log file.

Usage:
    from utils.logger import get_logger
    logger = get_logger("module_name")
    logger.info("message")
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from config import LOG_DIR, LOG_LEVEL

# Ensure log directory exists
_log_dir = Path(__file__).resolve().parent.parent / LOG_DIR
_log_dir.mkdir(parents=True, exist_ok=True)

# Log filename: split by day
_log_file = _log_dir / f"agent_{datetime.now().strftime('%Y-%m-%d')}.log"

# Log format
_FORMAT = "[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Configure root handler only once globally
_initialized = False


def _init_root():
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.DEBUG))

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (UTF-8, append mode)
    file_handler = logging.FileHandler(_log_file, encoding="utf-8", mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Suppress DEBUG logs from third-party libraries
    for noisy in ("httpcore", "httpx", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given module name."""
    _init_root()
    return logging.getLogger(name)
