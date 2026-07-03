"""
TradePro Backend - Main Server
Flask API server with Fyers integration.
Compatible with Python 3.11+, Termux, Linux.
"""

import time
import math
import random
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS

from backend.logger    import setup_logging, get_logger
from backend.config    import APP_ID, SECRET, REDIRECT_URL, validate, summary
from backend.pricing   import bs
from backend.response  import success, error
from backend.validators import (
    validate_symbol, validate_expiry, validate_strike_count,
    validate_quantity, validate_price, validate_strategy, validate_days,
)
from backend.fyers_service import FyersService

# ---------------------------------------------------------------------------
# Logging — must be first
# ---------------------------------------------------------------------------

setup_logging()
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config validation at startup
# ---------------------------------------------------------------------------

missing = validate()
if missing:
    logger.warning(f"Missing config fields: {missing} — running in MOCK mode")
else:
    logger.info(f"Config OK: {summary()}")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

_svc = FyersService(app_id=APP_ID, secret=SECRET, redirect_url=REDIRECT_URL)

# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

@app.before_request
def _log_request() -> None:
    logger.info(f"→ {request.method} {request.path} args={dict(request.args)}")


@app.after_request
def _log_response(resp):
    logger.info(f"← {request.method} {request.path} status={resp.status_code}")
    return resp

# ---------------------------------------------------------------------------
# Global error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(400)
def bad_request(e):
    return error(str(e), 400)


@app.errorhandler(404)
def not_found(e):
    return error("Endpoint not found", 404)


@app.errorhandler(405)
def method_not_allowed(e):
    return error("Method not allowed", 405)


@app.errorhandler(500)
def internal_error(e):
    logger.exception(f"Internal server error: {e}")
    return error("Internal server error", 500)

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.route("/api/health")
def health():
    return jsonify({
        "status"       : "ok",
        "authenticated": bool(_svc.token),
        "mock_mode"    : not bool(_svc.token),
        "version"      : "2.0.0",
    })

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/api/auth/url")
def auth_url():
    try:
        from fyers_apiv3 import fyersModel
        s = fyersModel.SessionModel(
            client_id=APP_ID, secret_key=SECRET,
            redirect_uri=REDIRECT_URL, response_type="code",
            grant_type="authorization_code",
        )
        return jsonify({"success": True, "url": s.generate_authcode()})
    except Exception as e:
        logger.error(f"Auth URL error: {e}")
        return error(str(e), 500)


@app.route("/api/auth/token", methods=["POST"])
def auth_token():
    try:
        from fyers_apiv3 import fyersModel
        s = fyersModel.SessionModel(
            client_id=APP_ID, secret_key=SECRET,
            redirect_uri=REDIRECT_URL, response_type="code",
            grant_type="authorization_code",
        )
        s.set_token(request.json.get("auth_code", ""))
        tok = s.generate_token().get("access_token", "")
        if tok:
            _svc.token = tok
            _svc._init_client()
            with open(".env", "a") as f:
                f.write(f"\nFYERS_ACCESS_TOKEN={tok}")
            logger.info("Token updated successfully")
            return jsonify({"success": True, "message": "Authenticated!"})
        return error("No token received", 400)
    except Exception as e:
        logger.error(f"Auth token error: {e}")
        return error(str(e), 500)

# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------

@app.route("/api/quotes")
def quotes():
    syms = request.args.get("symbols", "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,NSE:NIFTYMID100-INDEX")
    return jsonify(_svc.get_quotes(syms))

# ---------------------------------------------------------------------------
# Option Chain
# ---------------------------------------------------------------------------

@app.route("/api/optionchain")
def option_chain():
    symbol = request.args.get("symbol", "NIFTY")
    expiry = request.args.get("expiry", "")
    count  = request.args.get("strikecount", "10")

    # Validate
    ok, msg = validate_symbol(symbol)
    if not ok:
        return error(msg, 400)

    ok, msg = validate_expiry(expiry)
    if not ok:
        return error(msg, 400)

    ok, msg = validate_strike_count(count)
    if not ok:
        return error(msg, 400)

    return jsonify(_svc.get_option_chain(
        symbol=symbol,
        expiry=expiry,
        strike_count=int(count),
    ))

# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

@app.route("/api/positions")
def positions():
    return jsonify(_svc.get_positions())

# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@app.route("/api/orders")
def orders():
    return jsonify(_svc.get_orders())

# ---------------------------------------------------------------------------
# Place Order
# ---------------------------------------------------------------------------

@app.route("/api/placeorder", methods=["POST"])
def placeorder():
    data = request.json or {}

    ok, msg = validate_quantity(data.get("qty"))
    if not ok:
        return error(msg, 400)

    ok, msg = validate_price(data.get("limitPrice"))
    if not ok:
        return error(msg, 400)

    return jsonify(_svc.place_order(data))

# ---------------------------------------------------------------------------
# Funds
# ---------------------------------------------------------------------------

@app.route("/api/funds")
def funds():
    return jsonify(_svc.get_funds())

# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

@app.route("/api/backtest", methods=["POST"])
def backtest():
    b        = request.json or {}
    strategy = b.get("strategy", "straddle")
    days     = b.get("days", 90)
    sl_pct   = float(b.get("sl_pct", 50))
    tgt_pct  = float(b.get("tgt_pct", 50))
    lot_size = int(b.get("lot_size", 50))

    # Validate
    ok, msg = validate_strategy(strategy)
    if not ok:
        return error(msg, 400)

    ok, msg = validate_days(days)
    if not ok:
        return error(msg, 400)

    days = int(days)
    p    = 22480 * 0.88
    candles: list = []
    for i in range(days, -1, -1):
        p *= (1 + (random.random() - 0.47) * 0.012)
        candles.append({"c": round(p, 2), "t": int(time.time()) - i * 86400})

    trades: list  = []
    rpnl = peak   = 0.0
    mdd           = 0.0

    for day in candles:
        S   = day["c"]
        iv  = 0.13 + random.random() * 0.06
        atm = round(S / 100) * 100
        T   = 7 / 365
        r   = 0.065

        if strategy == "straddle":
            prem = bs(S, atm, T, r, iv, "call") + bs(S, atm, T, r, iv, "put")
        elif strategy == "strangle":
            prem = bs(S, atm + 200, T, r, iv, "call") + bs(S, atm - 200, T, r, iv, "put")
        elif strategy == "ironCondor":
            prem = (bs(S, atm + 200, T, r, iv, "call") - bs(S, atm + 400, T, r, iv, "call")) + \
                   (bs(S, atm - 200, T, r, iv, "put")  - bs(S, atm - 400, T, r, iv, "put"))
        elif strategy == "longCall":
            prem = -bs(S, atm, T, r, iv, "call")
        else:
            prem = -bs(S, atm, T, r, iv, "put")

        if abs(prem) < 0.5:
            continue

        move = (random.random() - 0.5) * 0.025
        if strategy in ["straddle", "strangle", "ironCondor"]:
            pnl = max(
                min(prem * lot_size * (0.6 if abs(move) < 0.012 else -0.4) * (0.5 + random.random()),
                    prem * tgt_pct / 100 * lot_size),
                -prem * sl_pct / 100 * lot_size,
            )
        else:
            ev  = bs(S * (1 + move), atm, max(T - 1 / 365, 0), r, iv * 0.95,
                     "call" if strategy == "longCall" else "put")
            pnl = max(
                min((ev - abs(prem)) * lot_size, abs(prem) * tgt_pct / 100 * lot_size),
                -abs(prem) * sl_pct / 100 * lot_size,
            )

        pnl   = round(pnl, 2)
        rpnl += pnl
        peak  = max(peak, rpnl)
        mdd   = min(mdd, rpnl - peak)

        trades.append({
            "date": datetime.fromtimestamp(day["t"]).strftime("%d %b"),
            "spot": round(S, 2),
            "iv"  : round(iv * 100, 1),
            "prem": round(abs(prem), 2),
            "pnl" : pnl,
            "win" : pnl > 0,
        })

    wins   = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    tot    = len(trades)
    eq     = 0.0
    equity = []
    for t in trades:
        eq += t["pnl"]
        equity.append({"date": t["date"], "equity": round(eq, 2)})

    return jsonify({
        "success": True,
        "summary": {
            "total"        : tot,
            "wins"         : len(wins),
            "losses"       : len(losses),
            "win_rate"     : round(len(wins) / tot * 100, 1) if tot else 0,
            "total_pnl"    : round(rpnl, 2),
            "max_drawdown" : round(mdd, 2),
            "avg_win"      : round(sum(t["pnl"] for t in wins)   / len(wins),   2) if wins   else 0,
            "avg_loss"     : round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
            "profit_factor": round(abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)), 2)
                             if losses and sum(t["pnl"] for t in losses) != 0 else 0,
            "sharpe"       : round(rpnl / (abs(mdd) + 1) * 0.5, 2),
        },
        "trades"      : trades[-50:],
        "equity_curve": equity,
    })

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 50)
    print("  TradePro Backend v2.0")
    print(f"  Mode   : {'LIVE' if _svc.token else 'MOCK'}")
    print(f"  Server : http://localhost:8000")
    print(f"  Health : http://localhost:8000/api/health")
    print("=" * 50)
    app.run(host="0.0.0.0", port=8000, debug=False, use_reloader=False)
