"""Fyers authentication routes."""

from flask import Blueprint, jsonify, request

from backend.config import APP_ID, SECRET, REDIRECT_URL
from backend.response import error
from backend.logger import get_logger
from backend.routes._ctx import get_ctx

auth_bp = Blueprint("auth", __name__)
logger = get_logger(__name__)


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
            with open(".env", "a") as f:
                f.write(f"\nFYERS_ACCESS_TOKEN={tok}")
            logger.info("Token updated successfully")
            return jsonify({"success": True, "message": "Authenticated!"})
        return error("No token received", 400)
    except Exception as e:
        logger.error(f"Auth token error: {e}")
        return error(str(e), 500)
