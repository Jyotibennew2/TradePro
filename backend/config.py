"""
TradePro Backend - Configuration
Loads and validates environment variables from .env file.
Compatible with Python 3.11+, Termux, Linux.
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# .env loader (no python-dotenv dependency)
# ---------------------------------------------------------------------------

def _load_env(env_path: Path) -> None:
    """Parse and load a .env file into os.environ."""
    if not env_path.exists():
        logger.warning(f".env file not found at {env_path}")
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    logger.debug(f"Loaded .env from {env_path}")


_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_load_env(_ENV_FILE)

# ---------------------------------------------------------------------------
# Configuration values
# ---------------------------------------------------------------------------

APP_ID       : str = os.environ.get("FYERS_APP_ID", "")
SECRET       : str = os.environ.get("FYERS_SECRET_KEY", "")
TOKEN        : str = os.environ.get("FYERS_ACCESS_TOKEN", "")
REDIRECT_URL : str = os.environ.get("REDIRECT_URL", "http://127.0.0.1:8080/")
CLIENT_ID    : str = os.environ.get("FYERS_CLIENT_ID", "")
PIN          : str = os.environ.get("FYERS_PIN", "")
TOTP_KEY     : str = os.environ.get("FYERS_TOTP_KEY", "")

# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------

_REQUIRED: dict[str, str] = {
    "FYERS_APP_ID"    : APP_ID,
    "FYERS_SECRET_KEY": SECRET,
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate() -> list[str]:
    """
    Validate all required config fields.
    Returns list of missing field names (empty = all good).
    """
    missing = []
    for name, val in _REQUIRED.items():
        if not val:
            missing.append(name)
            logger.error(f"Missing required config: {name}")
    if not missing:
        logger.info("Config validation passed")
    return missing


def is_configured() -> bool:
    """Return True only if all required fields are present."""
    return bool(APP_ID and SECRET and TOKEN)


def summary() -> dict:
    """Return config summary — safe to log (no secrets)."""
    return {
        "app_id"      : APP_ID,
        "token_set"   : bool(TOKEN),
        "redirect_url": REDIRECT_URL,
        "configured"  : is_configured(),
    }
