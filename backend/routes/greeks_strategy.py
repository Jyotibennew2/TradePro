"""Options Greeks and strategy P&L calculation routes."""

from flask import Blueprint, jsonify, request

from backend.response import error
from backend.logger import get_logger
from backend.greeks import GreeksEngine
from backend.strategy import StrategyEngine

greeks_strategy_bp = Blueprint("greeks_strategy", __name__)
logger = get_logger(__name__)


@greeks_strategy_bp.route("/api/greeks")
def greeks():
    try:
        S    = float(request.args.get("spot",    24300))
        K    = float(request.args.get("strike",  24300))
        T    = float(request.args.get("expiry",  30)) / 365
        r    = float(request.args.get("rate",    6.5)) / 100
        iv   = float(request.args.get("iv",      15)) / 100
        otype= request.args.get("type", "call").lower()
        mkt  = request.args.get("market_price")
        mkt_price = float(mkt) if mkt else None

        result = GreeksEngine.calculate(S, K, T, r, iv, otype, mkt_price)
        return jsonify({"success": True, "data": result.to_dict()})
    except Exception as e:
        logger.error(f"Greeks error: {e}")
        return error(str(e), 400)


@greeks_strategy_bp.route("/api/strategy")
def strategy():
    try:
        S  = float(request.args.get("spot",   24300))
        T  = float(request.args.get("expiry", 30)) / 365
        r  = float(request.args.get("rate",   6.5)) / 100
        iv = float(request.args.get("iv",     15))  / 100
        name = request.args.get("name", "all").lower()

        atm = round(S / 100) * 100

        if name == "all":
            data = StrategyEngine.all_strategies(S, T, r, iv)
        elif name == "longcall":
            data = StrategyEngine.long_call(S, atm, T, r, iv).to_dict()
        elif name == "longput":
            data = StrategyEngine.long_put(S, atm, T, r, iv).to_dict()
        elif name == "straddle":
            data = StrategyEngine.short_straddle(S, atm, T, r, iv).to_dict()
        elif name == "strangle":
            data = StrategyEngine.short_strangle(S, atm, T, r, iv).to_dict()
        elif name == "ironcondor":
            data = StrategyEngine.iron_condor(S, atm, T, r, iv).to_dict()
        elif name == "ironfly":
            data = StrategyEngine.iron_fly(S, atm, T, r, iv).to_dict()
        elif name == "bullcall":
            data = StrategyEngine.bull_call_spread(S, atm, atm+200, T, r, iv).to_dict()
        elif name == "bearput":
            data = StrategyEngine.bear_put_spread(S, atm, atm-200, T, r, iv).to_dict()
        else:
            return error(f"Unknown strategy: {name}", 400)

        return jsonify({"success": True, "data": data})
    except Exception as e:
        logger.error(f"Strategy error: {e}")
        return error(str(e), 400)
