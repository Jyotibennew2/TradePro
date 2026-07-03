"""
TradePro Backend - Response Utility
Standard JSON response helpers.
Compatible with Python 3.11+, Termux, Linux.
"""

from flask import jsonify
from typing import Any, Optional


def success(data: Any = None, mock: bool = False, extra: Optional[dict] = None) -> Any:
    """
    Standard success response.
    {"success": true, "data": ..., "mock": false}
    """
    resp: dict = {"success": True}
    if data is not None:
        resp["data"] = data
    resp["mock"] = mock
    if extra:
        resp.update(extra)
    return jsonify(resp)


def error(message: str, code: int = 400, extra: Optional[dict] = None) -> Any:
    """
    Standard error response.
    {"success": false, "error": "...", "code": 400}
    """
    resp: dict = {
        "success": False,
        "error"  : message,
        "code"   : code,
    }
    if extra:
        resp.update(extra)
    return jsonify(resp), code
