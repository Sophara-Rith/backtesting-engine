"""
logger.py — Centralized logging configuration for the backtesting engine.

Configures:
- Console handler at INFO level (clean operational summary)
- File handler at DEBUG level (bar-by-bar tracing to logs/backtest_<timestamp>.log)
- Timestamp, module name, log level, and message formatting.
"""

from datetime import datetime
import logging
import os
from pathlib import Path
import sys
from typing import Optional

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_is_logging_configured = False


def setup_logging(
    log_dir: str = "logs",
    timestamp: Optional[str] = None,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> tuple[logging.Logger, Path]:
    """
    Initialize standard library logging with console (INFO) and timestamped file (DEBUG) handlers.

    Parameters:
        log_dir: Directory where log files are written. Defaults to 'logs'.
        timestamp: Optional timestamp string. Defaults to current UTC/local YYYYMMDD_HHMMSS.
        console_level: Logging level for stdout handler. Defaults to INFO.
        file_level: Logging level for file handler. Defaults to DEBUG.

    Returns:
        tuple of (root_logger, log_file_path)
    """
    global _is_logging_configured

    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    log_path = Path(log_dir) / f"backtest_{timestamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()

    # If already configured, avoid attaching duplicate handlers
    if not _is_logging_configured:
        root_logger.setLevel(logging.DEBUG)

        formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

        # Console Handler: INFO level
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # File Handler: DEBUG level, new timestamped file per run
        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setLevel(file_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        _is_logging_configured = True
        root_logger.debug(f"Logging initialized. Log file: {log_path}")

    return root_logger, log_path


def get_logger(name: str) -> logging.Logger:
    """
    Retrieve a logger instance scoped to the calling module.
    """
    return logging.getLogger(name)
