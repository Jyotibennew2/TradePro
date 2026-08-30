"""
TradePro Backend - Saved Backtest Routes (Phase 1)

Thin CRUD wrapper over backend/services/backtest_store.py. No backtest
math lives here — this only saves/lists/fetches/deletes what the existing
backtest routes already computed and returned to the frontend.
"""

from flask import Blueprint, jsonify, request

from backend.response import error
from backend.logger import get_logger
from backend.services import backtest_store

saved_backtests_bp = Blueprint("saved_backtests", __name__)
logger = get_logger(__name__)


@saved_backtests_bp.route("/api/backtest/save", methods=["POST"])
def save_backtest():
    """
    Save a backtest result the frontend already has, so it can be reopened
    later. Body:
      kind        : "single" | "compare" | "batch" | "batch_realdata" | "walkforward"
      request     : the params object that was sent to run the backtest (required)
      result      : the response the backtest endpoint returned (required)
      label       : optional user-given name for this saved run
      symbol      : optional, for display in the saved-runs list
      data_source : optional "LIVE" | "MOCK", for display in the saved-runs list
    """
    try:
        b = request.json or {}
        kind    = b.get("kind")
        req_obj = b.get("request")
        result  = b.get("result")

        if not kind:
            return error("kind is required", 400)
        if req_obj is None or result is None:
            return error("request and result are both required", 400)

        backtest_id = backtest_store.save_backtest(
            kind=kind, request=req_obj, result=result,
            label=b.get("label"), symbol=b.get("symbol"), data_source=b.get("data_source"),
        )
        return jsonify({"success": True, "id": backtest_id})
    except Exception as e:
        logger.error(f"Save backtest error: {e}")
        return error(str(e), 400)


@saved_backtests_bp.route("/api/backtest/saved", methods=["GET"])
def list_saved_backtests():
    """Lightweight history list (no request/result payloads — see /saved/<id> for that)."""
    try:
        limit = int(request.args.get("limit", 100))
        return jsonify({"success": True, "data": backtest_store.list_backtests(limit=limit)})
    except Exception as e:
        logger.error(f"List saved backtests error: {e}")
        return error(str(e), 400)


@saved_backtests_bp.route("/api/backtest/saved/<int:backtest_id>", methods=["GET"])
def get_saved_backtest(backtest_id: int):
    """Full saved run, including the original request params and result payload."""
    record = backtest_store.get_backtest(backtest_id)
    if not record:
        return error(f"No saved backtest with id {backtest_id}", 404)
    return jsonify({"success": True, "data": record})


@saved_backtests_bp.route("/api/backtest/saved/<int:backtest_id>", methods=["DELETE"])
def delete_saved_backtest(backtest_id: int):
    deleted = backtest_store.delete_backtest(backtest_id)
    if not deleted:
        return error(f"No saved backtest with id {backtest_id}", 404)
    return jsonify({"success": True, "deleted": backtest_id})
