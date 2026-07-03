"""
TradePro Backend - Configuration
Loads all environment variables from .env file.
Compatible with Python 3.11+, Termux, Linux.
"""

import os
from pathlib import Path


# ---------------------------------------------------------------------------
# .env loader (no python-dotenv dependency)
# ---------------------------------------------------------------------------

def _load_env(env_path: Path) -> None:
    """Parse and load a .env file into os.environ."""
    if not env_path.exists():
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


# Load .env from TradePro root (one level above backend/)
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_load_env(_ENV_FILE)


# ---------------------------------------------------------------------------
# Configuration values
# ---------------------------------------------------------------------------

APP_ID       = os.environ.get("FYERS_APP_ID", "")
SECRET       = os.environ.get("FYERS_SECRET_KEY", "")
TOKEN        = os.environ.get("FYERS_ACCESS_TOKEN", "")
REDIRECT_URL = os.environ.get("REDIRECT_URL", "http://127.0.0.1:8080/")

# Derived
CLIENT_ID    = os.environ.get("FYERS_CLIENT_ID", "")
PIN          = os.environ.get("FYERS_PIN", "")
TOTP_KEY     = os.environ.get("FYERS_TOTP_KEY", "")


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    """Return True only if all required fields are present."""
    return bool(APP_ID and SECRET and TOKEN)


def summary() -> dict:
    """Return config summary — safe to log (no secrets)."""
    return {
        "app_id":       APP_ID,
        "token_set":    bool(TOKEN),
        "redirect_url": REDIRECT_URL,
        "configured":   is_configured(),
    }
