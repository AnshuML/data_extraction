"""
Structured, coloured logger for the entire pipeline.
One call to get_logger() per module — consistent format everywhere.
"""
import logging
import os
import sys
from datetime import datetime
from config import LOG_DIR

os.makedirs(LOG_DIR, exist_ok=True)

_LOG_COLORS = {
    "DEBUG":    "\033[36m",   # Cyan
    "INFO":     "\033[32m",   # Green
    "WARNING":  "\033[33m",   # Yellow
    "ERROR":    "\033[31m",   # Red
    "CRITICAL": "\033[35m",   # Magenta
    "RESET":    "\033[0m",
}


class ColorFormatter(logging.Formatter):
    FMT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        color  = _LOG_COLORS.get(record.levelname, "")
        reset  = _LOG_COLORS["RESET"]
        record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Console handler — coloured
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(ColorFormatter(ColorFormatter.FMT, datefmt="%H:%M:%S"))

    # File handler — plain text, full debug
    log_file = os.path.join(
        LOG_DIR,
        f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"
    )
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(ColorFormatter.FMT, datefmt="%Y-%m-%d %H:%M:%S"))

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger
