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
MAX_BYTES    : int  = 10 * 1024 * 1024  # 10 MB per file
BACKUP_COUNT : int  = 10                # keep last 10 rotated files
LOG_FORMAT   : str  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT  : str  = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# Secrets masking filter
# ---------------------------------------------------------------------------

class _MaskSecretsFilter(logging.Filter):
    """Mask sensitive values in log records."""

    _MASK_KEYS = ("token", "secret", "pin", "totp", "password", "access_token")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for key in self._MASK_KEYS:
            if key in msg.lower():
                # Mask any value that looks like a long alphanumeric string
                import re
                msg = re.sub(
                    r'(["\']?' + key + r'["\']?\s*[:=]\s*["\']?)([A-Za-z0-9_\-]{6})([A-Za-z0-9_\-]*)([A-Za-z0-9_\-]{4})',
                    lambda m: m.group(1) + m.group(2) + "****" + m.group(4),
                    msg,
                    flags=re.IGNORECASE,
                )
        record.msg  = msg
        record.args = ()
        return True


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_logging(level: int = logging.INFO) -> None:
    """
    Call once at startup.
    Sets up:
      - RotatingFileHandler → logs/tradepro.log (10MB x 10)
      - StreamHandler       → console
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    mask      = _MaskSecretsFilter()

    # Rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    file_handler.addFilter(mask)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    console_handler.addFilter(mask)

    # Root logger
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call after setup_logging()."""
    return logging.getLogger(name)
