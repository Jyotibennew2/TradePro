"""Fyers authentication routes."""

import os
from pathlib import Path

from flask import Blueprint, jsonify, request

from backend.config import APP_ID, SECRET, REDIRECT_URL
from backend.response import error
from backend.logger import get_logger
from backend.routes._ctx import get_ctx

auth_bp = Blueprint("auth", __name__)
logger = get_logger(__name__)

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


def _persist_token(tok: str) -> None:
    """
    Write FYERS_ACCESS_TOKEN into .env, replacing any existing line for that
    key instead of appending a duplicate. Old behavior appended a new line
    every time, leaving stale duplicate tokens in the file; since config.py's
    loader keeps the *first* occurrence of a key, that meant a restart could
    silently reload an old, stale token even after a successful re-login.
    """
    lines: list[str] = []
    if _ENV_PATH.exists():
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

    replaced = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith("FYERS_ACCESS_TOKEN="):
            new_lines.append(f"FYERS_ACCESS_TOKEN={tok}\n")
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        new_lines.append(f"FYERS_ACCESS_TOKEN={tok}\n")

    with open(_ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Keep the in-memory env var in sync too, for any code path that reads
    # os.environ directly rather than the already-updated svc.token.
    os.environ["FYERS_ACCESS_TOKEN"] = tok


@auth_bp.route("/api/auth/url")
def auth_url():
    try:
        from fyers_apiv3 import fyersModel
        s = fyersModel.SessionModel(
            client_id=APP_ID, secret_key=SECRET,
            redirect_uri=REDIRECT_URL, response_type="code",
            grant_type="authorization_code",
        )
        return jsonify({"success": True, "url": s.generate_authcode()})
    except Exception as e:
        logger.error(f"Auth URL error: {e}")
        return error(str(e), 500)


@auth_bp.route("/api/auth/token", methods=["POST"])
def auth_token():
    try:
        from fyers_apiv3 import fyersModel
        s = fyersModel.SessionModel(
            client_id=APP_ID, secret_key=SECRET,
            redirect_uri=REDIRECT_URL, response_type="code",
            grant_type="authorization_code",
        )
        s.set_token(request.json.get("auth_code", ""))
        tok = s.generate_token().get("access_token", "")
        if tok:
            svc = get_ctx()["svc"]
            svc.token = tok
            svc._init_client()
            _persist_token(tok)
            logger.info("Token updated successfully (existing .env entry replaced, not appended)")
            return jsonify({"success": True, "message": "Authenticated!"})
        return error("No token received", 400)
    except Exception as e:
        logger.error(f"Auth token error: {e}")
        return error(str(e), 500)
