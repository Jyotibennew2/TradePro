"""
TradePro Backend - Request Middleware
UUID request tracking, execution time logging.
Compatible with Python 3.11+, Termux, Linux.
"""

import time
import uuid
import logging
from flask import Flask, g, request

logger = logging.getLogger(__name__)


def register_middleware(app: Flask) -> None:
    """Register before/after request hooks on the Flask app."""

    @app.before_request
    def _before() -> None:
        g.request_id  = str(uuid.uuid4())[:8]
        g.start_time  = time.perf_counter()
        logger.info(
            f"[{g.request_id}] → {request.method} {request.path} "
            f"args={dict(request.args)}"
        )

    @app.after_request
    def _after(resp):
        elapsed_ms = round((time.perf_counter() - g.start_time) * 1000, 2)
        logger.info(
            f"[{g.request_id}] ← {request.method} {request.path} "
            f"status={resp.status_code} time={elapsed_ms}ms"
        )
        resp.headers["X-Request-ID"]    = g.request_id
        resp.headers["X-Response-Time"] = f"{elapsed_ms}ms"
        return resp
