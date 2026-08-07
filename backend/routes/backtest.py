"""Backtest routes: synthetic (Black-Scholes) backtest and real walk-forward backtest."""

import random
from datetime import datetime
from flask import Blueprint, jsonify, request

from backend.response import error
from backend.logger import get_logger
from backend.pricing import bs
from backend.validators import validate_symbol, validate_strategy, validate_days, validate_resolution
from backend.services import chain_archive
from backend.routes._ctx import get_ctx

backtest_bp = Blueprint("backtest", __name__)
logger = get_logger(__name__)


@backtest_bp.route("/api/backtest", methods=["POST"])
def backtest():
    market = get_ctx()["market"]
    b          = request.json or {}
    symbol     = b.get("symbol", "NIFTY")
    strategy   = b.get("strategy", "straddle")
    days       = b.get("days", 90)
    resolution = b.get("resolution", "1d")
    sl_pct     = float(b.get("sl_pct", 50))
    tgt_pct    = float(b.get("tgt_pct", 50))
    lot_size   = int(b.get("lot_size", 50))

    ok, msg = validate_symbol(symbol)
    if not ok:
        return error(msg, 400)
    ok, msg = validate_strategy(strategy)
    if not ok:
        return error(msg, 400)
    ok, msg = validate_days(days)
    if not ok:
        return error(msg, 400)
    ok, msg = validate_resolution(resolution)
    if not ok:
        return error(msg, 400)

    days = int(days)
    hist = market.get_historical(symbol, days=days, interval=resolution)
    raw_candles = hist.get("candles", [])
    if not raw_candles:
        return error("No historical data available", 500)
    candles      = [{"c": c["close"], "t": c["t"]} for c in raw_candles]
    is_mock_data = bool(hist.get("mock", True))
    is_intraday  = resolution != "1d"
    date_fmt     = "%d %b %H:%M" if is_intraday else "%d %b"

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
            "date": datetime.fromtimestamp(day["t"]).strftime(date_fmt),
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
        "success"    : True,
        "symbol"     : symbol,
        "resolution" : resolution,
        "data_source": "MOCK" if is_mock_data else "LIVE",
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


@backtest_bp.route("/api/backtest/walkforward", methods=["POST"])
def backtest_walkforward():
    """
    Replays a specific multi-leg strategy forward through REAL archived
    option-chain snapshots (not Black-Scholes) starting from an entry point,
    applying SL/target rules against the actual premium changes that
    happened. Only works for dates/expiries TradePro has archived data for.

    Body:
      symbol     : "NIFTY" | "BANKNIFTY"
      expiry     : contract expiry, YYYY-MM-DD
      entry_time : unix epoch seconds — the snapshot to enter at
      legs       : [{ "strike": 24300, "option_type": "CE"|"PE",
                       "action": "BUY"|"SELL", "lots": 1 }, ...]
      lot_size   : contract lot size (e.g. 50 for NIFTY)
      sl_pct     : stop loss, % of entry premium (e.g. 50)
      tgt_pct    : target, % of entry premium (e.g. 50)
      exit_time  : optional unix epoch — hard cutoff even if SL/target not hit
    """
    try:
        b          = request.json or {}
        symbol     = b.get("symbol", "NIFTY")
        expiry     = b.get("expiry", "")
        entry_time = b.get("entry_time")
        legs       = b.get("legs", [])
        lot_size   = int(b.get("lot_size", 50))
        sl_pct     = float(b.get("sl_pct", 50))
        tgt_pct    = float(b.get("tgt_pct", 50))
        exit_time  = b.get("exit_time")

        ok, msg = validate_symbol(symbol)
        if not ok:
            return error(msg, 400)
        if not expiry or not entry_time or not legs:
            return error("expiry, entry_time and legs are required", 400)

        entry_time = int(entry_time)
        exit_time  = int(exit_time) if exit_time else None

        snapshots = chain_archive.list_snapshots_range(symbol, expiry, entry_time, exit_time)
        if not snapshots:
            return error(f"No archived snapshots found for {symbol} exp={expiry} from that entry time onward", 404)

        def leg_price(snapshot: dict, strike: float, otype: str) -> float | None:
            for r in snapshot["rows"]:
                if r["strike"] == strike:
                    return r.get("ce_ltp") if otype == "CE" else r.get("pe_ltp")
            return None

        entry_snap = snapshots[0]
        entry_prices: dict[int, float] = {}
        entry_premium_abs = 0.0
        for i, leg in enumerate(legs):
            p = leg_price(entry_snap, leg["strike"], leg["option_type"])
            if p is None:
                return error(f"Strike {leg['strike']} {leg['option_type']} not found in entry snapshot (outside archived range)", 400)
            entry_prices[i] = p
            qty = int(leg.get("lots", 1)) * lot_size
            entry_premium_abs += p * qty

        sl_amount  = entry_premium_abs * sl_pct  / 100
        tgt_amount = entry_premium_abs * tgt_pct / 100

        equity_curve = []
        exit_reason  = "data_ended"
        exit_snap    = entry_snap
        is_mock      = bool(entry_snap.get("mock", True))

        for snap in snapshots:
            pnl = 0.0
            missing = False
            for i, leg in enumerate(legs):
                p = leg_price(snap, leg["strike"], leg["option_type"])
                if p is None:
                    missing = True
                    break
                qty  = int(leg.get("lots", 1)) * lot_size
                sign = 1 if leg["action"] == "BUY" else -1
                pnl += (p - entry_prices[i]) * qty * sign
            if missing:
                continue

            equity_curve.append({"t": snap["t"], "pnl": round(pnl, 2), "spot": snap["spot"]})
            exit_snap = snap

            if pnl <= -sl_amount:
                exit_reason = "SL Hit"
                break
            if pnl >= tgt_amount:
                exit_reason = "Target Hit"
                break

        final_pnl = equity_curve[-1]["pnl"] if equity_curve else 0.0

        return jsonify({
            "success"          : True,
            "symbol"           : symbol,
            "expiry"           : expiry,
            "was_mock"         : is_mock,
            "entry"            : {"t": entry_snap["t"], "spot": entry_snap["spot"], "premium_abs": round(entry_premium_abs, 2)},
            "exit"             : {"t": exit_snap["t"], "spot": exit_snap["spot"], "reason": exit_reason},
            "sl_amount"        : round(sl_amount, 2),
            "tgt_amount"       : round(tgt_amount, 2),
            "final_pnl"        : round(final_pnl, 2),
            "equity_curve"     : equity_curve,
            "snapshots_used"   : len(equity_curve),
            "note"             : "Walk-forward: entry/exit premiums are real archived LTPs for these exact strikes, not simulated.",
        })
    except Exception as e:
        logger.error(f"Walk-forward backtest error: {e}")
        return error(str(e), 400)
