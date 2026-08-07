"""Technical scanner routes."""

import time
import math as _math
from flask import Blueprint, jsonify, request

from backend.response import error
from backend.logger import get_logger
from backend.validators import validate_symbol
from backend.scanner import ScannerEngine

scanner_bp = Blueprint("scanner", __name__)
logger = get_logger(__name__)


@scanner_bp.route("/api/scanner")
def scanner():
    try:
        symbol = request.args.get("symbol", "NIFTY")
        ok, msg = validate_symbol(symbol)
        if not ok:
            return error(msg, 400)

        # Generate mock price/volume data for scanning
        base   = {"NIFTY": 24300.0, "BANKNIFTY": 58000.0}.get(symbol.upper(), 24300.0)
        t      = time.time()
        prices = [round(base * (1 + _math.sin((t - i*60)/300)*0.02 + (i%7-3)*0.001), 2) for i in range(50, -1, -1)]
        volumes= [int(abs(800000 + _math.sin((t - i*60)/600)*300000 + (i%5)*50000)) for i in range(50, -1, -1)]
        ltp    = prices[-1]
        open_  = prices[0]
        high   = max(prices[-10:])
        low    = min(prices[-10:])
        prev_c = prices[-2] if len(prices) > 1 else ltp

        results = ScannerEngine.run_all(
            symbol=symbol, prices=prices, volumes=volumes,
            open_=open_, high=high, low=low, prev_close=prev_c,
        )
        return jsonify({"success": True, "symbol": symbol, "ltp": ltp, "data": results})
    except Exception as e:
        logger.error(f"Scanner error: {e}")
        return error(str(e), 500)
