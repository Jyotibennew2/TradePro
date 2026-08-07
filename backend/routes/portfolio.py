"""Portfolio summary routes."""

from flask import Blueprint, jsonify, request

from backend.response import error
from backend.portfolio import portfolio

portfolio_bp = Blueprint("portfolio", __name__)


@portfolio_bp.route("/api/portfolio")
def portfolio_api():
    action = request.args.get("action", "summary")
    if action == "summary":
        return jsonify({"success": True, "data": portfolio.summary()})
    elif action == "holdings":
        return jsonify({"success": True, "data": portfolio.current_holdings()})
    elif action == "realized":
        return jsonify({"success": True, "data": portfolio.realized_pnl()})
    elif action == "unrealized":
        return jsonify({"success": True, "data": portfolio.unrealized_pnl()})
    elif action == "daily":
        return jsonify({"success": True, "data": portfolio.daily_pnl().to_dict()})
    return error("Unknown action", 400)
