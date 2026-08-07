"""Health check endpoint."""

from flask import Blueprint, jsonify
from backend.routes._ctx import get_ctx

health_bp = Blueprint("health", __name__)


@health_bp.route("/api/health")
def health():
    svc = get_ctx()["svc"]
    return jsonify({
        "status"       : "ok",
        "authenticated": bool(svc.token),
        "mock_mode"    : not bool(svc.token),
        "version"      : "2.0.0",
    })
