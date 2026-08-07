"""Live trading routes: positions, orders, place order, funds."""

from flask import Blueprint, jsonify, request

from backend.response import error
from backend.validators import validate_quantity, validate_price
from backend.routes._ctx import get_ctx

trading_bp = Blueprint("trading", __name__)


@trading_bp.route("/api/positions")
def positions():
    svc = get_ctx()["svc"]
    return jsonify(svc.get_positions())


@trading_bp.route("/api/orders")
def orders():
    svc = get_ctx()["svc"]
    return jsonify(svc.get_orders())


@trading_bp.route("/api/placeorder", methods=["POST"])
def placeorder():
    svc = get_ctx()["svc"]
    data    = request.json or {}
    ok, msg = validate_quantity(data.get("qty"))
    if not ok:
        return error(msg, 400)
    ok, msg = validate_price(data.get("limitPrice"))
    if not ok:
        return error(msg, 400)
    return jsonify(svc.place_order(data))


@trading_bp.route("/api/funds")
def funds():
    svc = get_ctx()["svc"]
    return jsonify(svc.get_funds())
