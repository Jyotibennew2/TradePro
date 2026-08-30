"""Paper trading routes."""

from flask import Blueprint, jsonify, request

from backend.response import error
from backend.validators import validate_historical_symbol, validate_quantity, validate_price
from backend.paper_trade import paper_engine

papertrade_bp = Blueprint("papertrade", __name__)


@papertrade_bp.route("/api/papertrade", methods=["GET", "POST"])
def papertrade():
    if request.method == "GET":
        action = request.args.get("action", "portfolio")
        if action == "portfolio":
            return jsonify({"success": True, "data": paper_engine.portfolio()})
        elif action == "history":
            limit = int(request.args.get("limit", 50))
            return jsonify({"success": True, "data": paper_engine.history(limit)})
        elif action == "reset":
            capital = float(request.args.get("capital", 500000))
            return jsonify(paper_engine.reset(capital))
        return error("Unknown action", 400)

    # POST — place order
    # validate_historical_symbol (not the option-chain-only validate_symbol)
    # so this route accepts BOTH the option-chain index shortcuts
    # (NIFTY/BANKNIFTY/MIDCPNIFTY) and fully-qualified NSE equity symbols
    # (e.g. "NSE:RELIANCE-EQ") — the same validator /api/historical already
    # uses for the Equity Quant Scanner. No behavior change for existing
    # options callers: every symbol validate_symbol accepted, this accepts too.
    data = request.json or {}
    ok, msg = validate_historical_symbol(data.get("symbol", "NIFTY"))
    if not ok:
        return error(msg, 400)
    ok, msg = validate_quantity(data.get("qty"))
    if not ok:
        return error(msg, 400)
    ok, msg = validate_price(data.get("entry_price"))
    if not ok:
        return error(msg, 400)

    result = paper_engine.place_order(
        symbol      = data.get("symbol",      "NIFTY"),
        option_type = data.get("option_type", "CE"),
        strike      = float(data.get("strike",      0)),
        expiry      = data.get("expiry",      ""),
        action      = data.get("action",      "BUY"),
        qty         = int(data.get("qty",     1)),
        entry_price = float(data.get("entry_price", 0)),
        sl          = float(data.get("sl",    0)),
        target      = float(data.get("target",0)),
    )
    return jsonify(result)


@papertrade_bp.route("/api/papertrade/exit", methods=["POST"])
def papertrade_exit():
    data       = request.json or {}
    order_id   = data.get("order_id", "")
    exit_price = float(data.get("exit_price", 0))
    if not order_id:
        return error("order_id is required", 400)
    return jsonify(paper_engine.exit_order(order_id, exit_price))


@papertrade_bp.route("/api/papertrade/modify", methods=["POST"])
def papertrade_modify():
    data     = request.json or {}
    order_id = data.get("order_id", "")
    if not order_id:
        return error("order_id is required", 400)
    return jsonify(paper_engine.modify_order(
        order_id = order_id,
        sl       = float(data["sl"])     if "sl"     in data else None,
        target   = float(data["target"]) if "target" in data else None,
    ))
