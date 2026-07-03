"""
TradePro Backend - Logger
Rotating file logger + console logger.
Compatible with Python 3.11+, Termux, Linux.
"""

import logging
import logging.handlers
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_DIR      : Path = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE     : Path = LOG_DIR / "tradepro.log"
MAX_BYTES    : int  = 5 * 1024 * 1024   # 5 MB per file
BACKUP_COUNT : int  = 3                  # keep last 3 rotated files
LOG_FORMAT   : str  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT  : str  = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_logging(level: int = logging.INFO) -> None:
    """
    Call once at startup.
    Sets up:
      - RotatingFileHandler  → logs/tradepro.log
      - StreamHandler        → console
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # Root logger
    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on reload
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(console_handler)
    else:
        root.handlers.clear()
        root.addHandler(file_handler)
        root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call after setup_logging()."""
    return logging.getLogger(name)
