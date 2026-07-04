"""
TradePro Backend - Global Error Handler
Handles all HTTP errors and unhandled exceptions.
Never exposes Python tracebacks to clients.
Compatible with Python 3.11+, Termux, Linux.
"""

import traceback
import logging
from flask import Flask, jsonify

logger = logging.getLogger(__name__)


def _err(message: str, code: int):
    return jsonify({"success": False, "error": message, "code": code}), code


def register_error_handlers(app: Flask) -> None:
    """Register all error handlers on the Flask app."""

    @app.errorhandler(400)
    def bad_request(e):
        logger.warning(f"400 Bad Request: {e}")
        return _err(str(e.description) if hasattr(e, "description") else "Bad request", 400)

    @app.errorhandler(401)
    def unauthorized(e):
        logger.warning(f"401 Unauthorized: {e}")
        return _err("Unauthorized", 401)

    @app.errorhandler(403)
    def forbidden(e):
        logger.warning(f"403 Forbidden: {e}")
        return _err("Forbidden", 403)

    @app.errorhandler(404)
    def not_found(e):
        logger.warning(f"404 Not Found: {e}")
        return _err("Endpoint not found", 404)

    @app.errorhandler(405)
    def method_not_allowed(e):
        logger.warning(f"405 Method Not Allowed: {e}")
        return _err("Method not allowed", 405)

    @app.errorhandler(429)
    def too_many_requests(e):
        logger.warning(f"429 Rate Limited: {e}")
        return _err("Too many requests — please slow down", 429)

    @app.errorhandler(500)
    def internal_error(e):
        logger.error(f"500 Internal Error: {e}\n{traceback.format_exc()}")
        return _err("Internal server error", 500)

    @app.errorhandler(Exception)
    def unhandled_exception(e):
        logger.error(f"Unhandled exception: {e}\n{traceback.format_exc()}")
        return _err("An unexpected error occurred", 500)
