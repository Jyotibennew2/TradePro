"""Market data routes: quotes, option chain (live/historical/archive), historical candles."""

import time
from flask import Blueprint, jsonify, request

from backend.response import error
from backend.logger import get_logger
from backend.pricing import BlackScholes
from backend.validators import validate_symbol, validate_historical_symbol, validate_expiry, validate_strike_count, validate_resolution
from backend.services import chain_archive
from backend.routes._ctx import get_ctx

market_data_bp = Blueprint("market_data", __name__)
logger = get_logger(__name__)


@market_data_bp.route("/api/quotes")
def quotes():
    market = get_ctx()["market"]
    syms = request.args.get("symbols", "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,NSE:NIFTYMID100-INDEX")
    return jsonify(market.get_quotes(syms))


@market_data_bp.route("/api/optionchain")
def option_chain():
    market = get_ctx()["market"]
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

    return jsonify(market.get_option_chain(
        symbol=symbol, expiry=expiry, strike_count=int(count)
    ))


@market_data_bp.route("/api/optionchain/expiries")
def option_chain_expiries():
    market = get_ctx()["market"]
    symbol = request.args.get("symbol", "NIFTY")
    ok, msg = validate_symbol(symbol)
    if not ok:
        return error(msg, 400)
    return jsonify(market.get_expiries(symbol))


@market_data_bp.route("/api/optionchain/historical")
def option_chain_historical():
    """
    Fyers (and most retail broker APIs) do not offer real historical
    option-chain snapshots — expired option contracts simply aren't
    queryable after expiry. Instead, this reconstructs a *theoretical*
    chain for a given historical spot price using Black-Scholes, so a
    trader can see what strikes/premiums/Greeks would plausibly have
    looked like on that day. Always returned with reconstructed=True so
    the frontend can label it clearly (never confuse this with a real quote).
    """
    try:
        symbol         = request.args.get("symbol", "NIFTY")
        spot           = float(request.args.get("spot", 0))
        iv             = float(request.args.get("iv", 15)) / 100
        days_to_expiry = float(request.args.get("days_to_expiry", 7))
        strike_count   = int(request.args.get("strikecount", 10))
        rate           = float(request.args.get("rate", 6.5)) / 100
        label          = request.args.get("label", "")

        ok, msg = validate_symbol(symbol)
        if not ok:
            return error(msg, 400)
        if spot <= 0:
            return error("spot must be a positive number", 400)
        if not (1 <= strike_count <= 20):
            return error("strikecount must be between 1 and 20", 400)

        T    = max(days_to_expiry, 0.5) / 365
        step = 100 if spot < 30000 else 200
        atm  = round(spot / step) * step

        rows = []
        for i in range(-strike_count, strike_count + 1):
            K  = atm + i * step
            ce = BlackScholes(spot, K, T, rate, iv, "call")
            pe = BlackScholes(spot, K, T, rate, iv, "put")
            rows.append({
                "strike"  : K,
                "ce_ltp"  : round(ce.price(), 2),
                "pe_ltp"  : round(pe.price(), 2),
                "ce_iv"   : round(iv * 100, 1),
                "pe_iv"   : round(iv * 100, 1),
                "ce_delta": round(ce.delta(), 3),
                "pe_delta": round(pe.delta(), 3),
                "ce_gamma": round(ce.gamma(), 5),
                "pe_gamma": round(pe.gamma(), 5),
                "ce_theta": round(ce.theta(), 2),
                "pe_theta": round(pe.theta(), 2),
                "ce_vega" : round(ce.vega(),  2),
                "pe_vega" : round(pe.vega(),  2),
                "atm"     : K == atm,
            })

        return jsonify({
            "success"       : True,
            "symbol"        : symbol,
            "spot"          : spot,
            "label"         : label,
            "reconstructed" : True,
            "note"          : "Reconstructed via Black-Scholes from historical spot — not a real historical quote",
            "data"          : {"expiryData": rows, "atmIndex": strike_count},
        })
    except Exception as e:
        logger.error(f"Historical option chain error: {e}")
        return error(str(e), 400)


@market_data_bp.route("/api/optionchain/archive")
def option_chain_archive():
    """
    Returns REAL saved option-chain snapshots for a given capture date and
    expiry. Unlike /historical (which is a Black-Scholes reconstruction),
    this is genuine live data that was captured and stored — full
    backtesting field set: timestamp, underlying price, expiry, strike,
    LTP, bid, ask, volume, OI, change in OI, IV, Delta, Gamma, Theta, Vega.

    Query params:
      symbol  - NIFTY / BANKNIFTY
      date    - capture date, YYYY-MM-DD (required)
      expiry  - contract expiry date, YYYY-MM-DD (optional — defaults to
                the nearest expiry that has data for this capture date)
      time    - unix epoch seconds (optional) — picks the snapshot closest
                to this exact moment (for stepping through the day's replay);
                omit for the last snapshot of the day (closing chain)
    """
    try:
        symbol = request.args.get("symbol", "NIFTY")
        date   = request.args.get("date", "")     # capture date YYYY-MM-DD
        expiry = request.args.get("expiry", "")   # contract expiry YYYY-MM-DD
        at     = request.args.get("time", "")     # unix epoch seconds

        ok, msg = validate_symbol(symbol)
        if not ok:
            return error(msg, 400)
        if not date:
            return error("date (YYYY-MM-DD) is required", 400)

        if not expiry:
            available = chain_archive.list_expiries_for_capture_date(symbol, date)
            if not available:
                return error(f"No archived data saved for {symbol} on {date}", 404)
            expiry = available[0]   # nearest (folders are sorted ascending)

        target_epoch = int(at) if at else None
        snapshot = chain_archive.nearest_snapshot(symbol, expiry, date, target_epoch)
        if not snapshot:
            return error(f"No archived data saved for {symbol} expiry {expiry} on {date}", 404)

        rows = [{
            "strike"      : r["strike"],
            "ce_ltp"      : r.get("ce_ltp"),      "pe_ltp"      : r.get("pe_ltp"),
            "ce_bid"      : r.get("ce_bid"),      "pe_bid"      : r.get("pe_bid"),
            "ce_ask"      : r.get("ce_ask"),      "pe_ask"      : r.get("pe_ask"),
            "ce_oi"       : r.get("ce_oi"),       "pe_oi"       : r.get("pe_oi"),
            "ce_oi_change": r.get("ce_oi_change"),"pe_oi_change": r.get("pe_oi_change"),
            "ce_volume"   : r.get("ce_volume"),   "pe_volume"   : r.get("pe_volume"),
            "ce_iv"       : r.get("ce_iv"),       "pe_iv"       : r.get("pe_iv"),
            "ce_delta"    : r.get("ce_delta"),    "pe_delta"    : r.get("pe_delta"),
            "ce_gamma"    : r.get("ce_gamma"),    "pe_gamma"    : r.get("pe_gamma"),
            "ce_theta"    : r.get("ce_theta"),    "pe_theta"    : r.get("pe_theta"),
            "ce_vega"     : r.get("ce_vega"),     "pe_vega"     : r.get("pe_vega"),
            "atm"         : r.get("atm", False),
        } for r in snapshot["rows"]]

        return jsonify({
            "success"             : True,
            "symbol"              : symbol,
            "date"                : date,
            "expiry"              : expiry,
            "spot"                : snapshot["spot"],
            "saved_at"            : snapshot["t"],
            "reconstructed"       : False,
            "was_mock"            : snapshot.get("mock", False),
            "days_to_expiry_used" : snapshot.get("days_to_expiry_used"),
            "note"                : "Real data captured and saved by TradePro for this specific expiry contract.",
            "data"                : {"expiryData": rows, "atmIndex": len(rows) // 2},
        })
    except Exception as e:
        logger.error(f"Option chain archive error: {e}")
        return error(str(e), 400)


@market_data_bp.route("/api/optionchain/archive/dates")
def option_chain_archive_dates():
    """
    Returns list of capture dates that have at least one real saved snapshot.
    Pass `expiry` (YYYY-MM-DD) to restrict to that specific expiry contract;
    omit it to get the union across all archived expiries.
    """
    symbol = request.args.get("symbol", "NIFTY")
    expiry = request.args.get("expiry", "") or None
    ok, msg = validate_symbol(symbol)
    if not ok:
        return error(msg, 400)
    return jsonify({
        "success": True, "symbol": symbol, "expiry": expiry,
        "dates"  : chain_archive.list_available_dates(symbol, expiry),
    })


@market_data_bp.route("/api/optionchain/archive/expiries")
def option_chain_archive_expiries():
    """
    Returns all expiry contracts that have ever been archived for this
    symbol. Pass `date` (capture date, YYYY-MM-DD) to instead get only the
    expiries that have a snapshot for that specific day.
    """
    symbol = request.args.get("symbol", "NIFTY")
    date   = request.args.get("date", "")
    ok, msg = validate_symbol(symbol)
    if not ok:
        return error(msg, 400)
    if date:
        expiries = chain_archive.list_expiries_for_capture_date(symbol, date)
    else:
        expiries = chain_archive.list_expiries(symbol)
    return jsonify({"success": True, "symbol": symbol, "date": date or None, "expiries": expiries})


@market_data_bp.route("/api/optionchain/archive/times")
def option_chain_archive_times():
    """
    Returns every captured_at timestamp (unix epoch seconds) available for
    a given symbol+expiry+capture date — used by the Simulator's replay/
    walk-forward controls to step forward and backward through the day at
    whatever granularity (5m/15m/30m/1h/2h/1d) the user picks.
    """
    symbol = request.args.get("symbol", "NIFTY")
    date   = request.args.get("date", "")
    expiry = request.args.get("expiry", "")
    ok, msg = validate_symbol(symbol)
    if not ok:
        return error(msg, 400)
    if not date or not expiry:
        return error("date and expiry (YYYY-MM-DD) are required", 400)
    return jsonify({
        "success": True, "symbol": symbol, "date": date, "expiry": expiry,
        "times"  : chain_archive.list_snapshot_times(symbol, expiry, date),
    })


@market_data_bp.route("/api/optionchain/archive/stats")
def option_chain_archive_stats():
    """Diagnostics: total rows stored and the SQLite file's size on disk (MB)."""
    return jsonify({"success": True, "data": chain_archive.db_stats()})


@market_data_bp.route("/api/historical")
def historical():
    """
    Historical candles. Accepts the index shortcuts (NIFTY/BANKNIFTY/
    MIDCPNIFTY) *or* any fully-qualified NSE equity symbol
    (e.g. NSE:RELIANCE-EQ) — used by the Equity Quant Scanner to pull
    per-stock candles for swing/momentum scoring.
    """
    market = get_ctx()["market"]
    symbol     = request.args.get("symbol", "NIFTY")
    days       = int(request.args.get("days", 30))
    resolution = request.args.get("resolution", "1d")

    ok, msg = validate_historical_symbol(symbol)
    if not ok:
        return error(msg, 400)
    ok, msg = validate_resolution(resolution)
    if not ok:
        return error(msg, 400)

    return jsonify(market.get_historical(symbol, days, interval=resolution))
