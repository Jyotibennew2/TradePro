"""
TradePro Backend - Main Server
Flask API server with Fyers integration.
Compatible with Python 3.11+, Termux, Linux.
"""

import time
import random
import logging
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS

from backend.logger         import setup_logging, get_logger
from backend.config         import APP_ID, SECRET, REDIRECT_URL, validate, summary
from backend.pricing        import bs
from backend.response       import success, error
from backend.middleware     import register_middleware
from backend.error_handler  import register_error_handlers
from backend.validators     import (
    validate_symbol, validate_expiry, validate_strike_count,
    validate_quantity, validate_price, validate_strategy, validate_days,
)
from backend.fyers_service      import FyersService
from backend.services.market_data import MarketDataService
from backend.greeks             import GreeksEngine
from backend.strategy           import StrategyEngine
from backend.scanner            import ScannerEngine
from backend.paper_trade        import paper_engine
from backend.portfolio          import portfolio
from backend.notification       import notification_service
from backend.scheduler          import scheduler
from backend.cache              import quote_cache, chain_cache

# ---------------------------------------------------------------------------
# Logging — must be first
# ---------------------------------------------------------------------------

setup_logging()
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

missing = validate()
if missing:
    logger.warning(f"Missing config fields: {missing} — running in MOCK mode")
else:
    logger.info(f"Config OK: {summary()}")

# ---------------------------------------------------------------------------
# App + services
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

_svc     = FyersService(app_id=APP_ID, secret=SECRET, redirect_url=REDIRECT_URL)
_market  = MarketDataService(_svc)

register_middleware(app)
register_error_handlers(app)

# ---------------------------------------------------------------------------
# Scheduler tasks
# ---------------------------------------------------------------------------

scheduler.add_task("refresh_quotes",  _market.refresh_quotes,               interval=3)
scheduler.add_task("refresh_nifty",   lambda: _market.refresh_chain("NIFTY"), interval=10)
scheduler.add_task("cache_cleanup",   lambda: (quote_cache.cleanup(), chain_cache.cleanup()), interval=60)
scheduler.start()

# ===========================================================================
# EXISTING APIs — DO NOT CHANGE RESPONSE FORMAT
# ===========================================================================

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
    return jsonify(_market.get_quotes(syms))

# ---------------------------------------------------------------------------
# Option Chain
# ---------------------------------------------------------------------------

@app.route("/api/optionchain")
def option_chain():
    symbol = request.args.get("symbol", "NIFTY")
    expiry = request.args.get("expiry", "")
    count  = request.args.get("strikecount", "10")

    ok, msg = validate_symbol(symbol)
    if not ok:
        return error(msg, 400)
    ok, msg = validate_expiry(expiry)
    if not ok:
        return error(msg, 400)
    ok, msg = validate_strike_count(count)
    if not ok:
        return error(msg, 400)

    return jsonify(_market.get_option_chain(
        symbol=symbol, expiry=expiry, strike_count=int(count)
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
    data    = request.json or {}
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

    ok, msg = validate_strategy(strategy)
    if not ok:
        return error(msg, 400)
    ok, msg = validate_days(days)
    if not ok:
        return error(msg, 400)

    days = int(days)
    hist = _market.get_historical("NIFTY", days=days, interval="1D")
    raw_candles = hist.get("candles", [])
    if not raw_candles:
        return error("No historical data available", 500)
    candles = [{"c": c["close"], "t": c["t"]} for c in raw_candles]

    trades: list = []
    rpnl = peak  = 0.0
    mdd          = 0.0

    for day in candles:
        S   = day["c"]
        iv  = 0.13 + random.random() * 0.06
        atm = round(S / 100) * 100
        T   = 7 / 365
        r   = 0.065

        if strategy == "straddle":
            prem = bs(S, atm, T, r, iv, "call") + bs(S, atm, T, r, iv, "put")
        elif strategy == "strangle":
            prem = bs(S, atm+200, T, r, iv, "call") + bs(S, atm-200, T, r, iv, "put")
        elif strategy == "ironCondor":
            prem = (bs(S, atm+200, T, r, iv, "call") - bs(S, atm+400, T, r, iv, "call")) + \
                   (bs(S, atm-200, T, r, iv, "put")  - bs(S, atm-400, T, r, iv, "put"))
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
            ev  = bs(S*(1+move), atm, max(T-1/365, 0), r, iv*0.95,
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
            "spot": round(S, 2), "iv": round(iv*100, 1),
            "prem": round(abs(prem), 2), "pnl": pnl, "win": pnl > 0,
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
            "win_rate"     : round(len(wins)/tot*100, 1) if tot else 0,
            "total_pnl"    : round(rpnl, 2),
            "max_drawdown" : round(mdd, 2),
            "avg_win"      : round(sum(t["pnl"] for t in wins)/len(wins), 2)   if wins   else 0,
            "avg_loss"     : round(sum(t["pnl"] for t in losses)/len(losses), 2) if losses else 0,
            "profit_factor": round(abs(sum(t["pnl"] for t in wins)/sum(t["pnl"] for t in losses)), 2)
                             if losses and sum(t["pnl"] for t in losses) != 0 else 0,
            "sharpe"       : round(rpnl/(abs(mdd)+1)*0.5, 2),
        },
        "trades"      : trades[-50:],
        "equity_curve": equity,
    })

# ===========================================================================
# NEW APIs — Sprint 3
# ===========================================================================

# ---------------------------------------------------------------------------
# Greeks
# ---------------------------------------------------------------------------

@app.route("/api/greeks")
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

# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

@app.route("/api/strategy")
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

# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

@app.route("/api/scanner")
def scanner():
    try:
        symbol = request.args.get("symbol", "NIFTY")
        ok, msg = validate_symbol(symbol)
        if not ok:
            return error(msg, 400)

        # Generate mock price/volume data for scanning
        import math as _math
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

# ---------------------------------------------------------------------------
# Paper Trade
# ---------------------------------------------------------------------------

@app.route("/api/papertrade", methods=["GET", "POST"])
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
    data = request.json or {}
    ok, msg = validate_symbol(data.get("symbol", "NIFTY"))
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


@app.route("/api/papertrade/exit", methods=["POST"])
def papertrade_exit():
    data       = request.json or {}
    order_id   = data.get("order_id", "")
    exit_price = float(data.get("exit_price", 0))
    if not order_id:
        return error("order_id is required", 400)
    return jsonify(paper_engine.exit_order(order_id, exit_price))


@app.route("/api/papertrade/modify", methods=["POST"])
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

# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

@app.route("/api/portfolio")
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

# ---------------------------------------------------------------------------
# Notification test
# ---------------------------------------------------------------------------

@app.route("/api/notification/test", methods=["POST"])
def notification_test():
    data    = request.json or {}
    channel = data.get("channel", "webhook")
    message = data.get("message", "TradePro test notification")

    if channel == "telegram":
        result = notification_service.send_telegram(message)
    elif channel == "webhook":
        result = notification_service.send_webhook({"message": message})
    elif channel == "whatsapp":
        result = notification_service.send_whatsapp(message)
    else:
        return error(f"Unknown channel: {channel}", 400)

    return jsonify(result.to_dict())

# ---------------------------------------------------------------------------
# Historical data
# ---------------------------------------------------------------------------

@app.route("/api/historical")
def historical():
    symbol = request.args.get("symbol", "NIFTY")
    days   = int(request.args.get("days", 30))
    ok, msg = validate_symbol(symbol)
    if not ok:
        return error(msg, 400)
    return jsonify(_market.get_historical(symbol, days))

# ---------------------------------------------------------------------------
# Scheduler status
# ---------------------------------------------------------------------------

@app.route("/api/scheduler")
def scheduler_status():
    return jsonify({"success": True, "data": scheduler.status()})

# ===========================================================================
# Main
# ===========================================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  TradePro Backend v3.0")
    print(f"  Mode   : {'LIVE' if _svc.token else 'MOCK'}")
    print(f"  Server : http://localhost:8000")
    print(f"  Health : http://localhost:8000/api/health")
    print("=" * 50)
    app.run(host="0.0.0.0", port=8000, debug=False, use_reloader=False)
