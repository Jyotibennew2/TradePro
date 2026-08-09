"""
TradePro Backend - Batch Backtest Route (V1)

POST /api/backtest/batch

Runs a sweep of backtests across multiple strategies, instruments,
timeframes, expiries and strikes in a single call, applies SL / trailing SL
/ greeks conditions, and returns results ranked by the chosen metric.

This route is orchestration only — all P&L math is delegated to
BatchBacktestEngine, which in turn calls the existing run_synthetic_backtest
/ run_walkforward_backtest functions. See backend/batch_backtest.py.
"""

from flask import Blueprint, jsonify, request

from backend.response import error
from backend.logger import get_logger
from backend.validators import validate_symbol, validate_strategy, validate_days, validate_resolution
from backend.routes._ctx import get_ctx
from backend.batch_backtest import BatchBacktestEngine

batch_backtest_bp = Blueprint("batch_backtest", __name__)
logger = get_logger(__name__)


def _validate_each(items, validator):
    for item in items:
        ok, msg = validator(item)
        if not ok:
            return False, msg
    return True, ""


@batch_backtest_bp.route("/api/backtest/batch", methods=["POST"])
def backtest_batch():
    """
    Body (synthetic sweep — multi-strategy/instrument/timeframe):
      strategies : ["straddle", "strangle", ...]   (required)
      symbols    : ["NIFTY", "BANKNIFTY", ...]      (required)
      resolutions: ["1d", "1h", ...]                (default ["1d"])
      days       : lookback days                    (default 90)
      lot_size   : contract lot size                (default 50)
      sl_pct     : stop loss %                       (default 50)
      tgt_pct    : target %                          (default 50)
      trailing_sl_pct : optional trailing SL %
      greeks_filter    : optional {"min_iv":..,"max_iv":..,"min_delta":..,"max_delta":..}
      rank_by    : "total_pnl" | "roi_pct" | "win_rate" | "max_drawdown"
                   | "profit_factor" | "risk_reward"   (default "total_pnl")

    Body (walk-forward sweep — multi-expiry/strike, uses real archived data):
      mode       : "walkforward"
      symbols    : ["NIFTY", ...]
      expiries   : ["2026-08-28", ...]
      strikes    : [24000, 24500, 25000, ...]
      entry_time : unix epoch seconds                (required)
      exit_time  : optional unix epoch
      option_type: "CE" | "PE"                        (default "CE")
      action     : "BUY" | "SELL"                      (default "BUY")
      lots       : int                                 (default 1)
      ... plus lot_size, sl_pct, tgt_pct, trailing_sl_pct, rank_by as above
    """
    try:
        b = request.json or {}
        mode     = b.get("mode", "synthetic")
        symbols  = b.get("symbols", [])
        lot_size = int(b.get("lot_size", 50))
        sl_pct   = float(b.get("sl_pct", 50))
        tgt_pct  = float(b.get("tgt_pct", 50))
        trailing_sl_pct = b.get("trailing_sl_pct")
        trailing_sl_pct = float(trailing_sl_pct) if trailing_sl_pct is not None else None
        greeks_filter   = b.get("greeks_filter")
        rank_by         = b.get("rank_by", "total_pnl")

        if not symbols:
            return error("symbols (non-empty list) is required", 400)
        ok, msg = _validate_each(symbols, validate_symbol)
        if not ok:
            return error(msg, 400)

        market = get_ctx()["market"]

        if mode == "walkforward":
            expiries   = b.get("expiries", [])
            strikes    = b.get("strikes", [])
            entry_time = b.get("entry_time")
            exit_time  = b.get("exit_time")
            option_type = b.get("option_type", "CE")
            action      = b.get("action", "BUY")
            lots        = int(b.get("lots", 1))

            if not expiries or not strikes or not entry_time:
                return error("expiries, strikes and entry_time are required for mode=walkforward", 400)

            engine = BatchBacktestEngine(
                market, lot_size=lot_size, sl_pct=sl_pct, tgt_pct=tgt_pct,
                trailing_sl_pct=trailing_sl_pct, greeks_filter=greeks_filter,
            )
            jobs = engine.build_walkforward_jobs(
                symbols, expiries, strikes, int(entry_time),
                int(exit_time) if exit_time else None, option_type, action, lots,
            )
        else:
            strategies  = b.get("strategies", [])
            resolutions = b.get("resolutions", ["1d"])
            days        = b.get("days", 90)

            if not strategies:
                return error("strategies (non-empty list) is required", 400)
            ok, msg = _validate_each(strategies, validate_strategy)
            if not ok:
                return error(msg, 400)
            ok, msg = _validate_each(resolutions, validate_resolution)
            if not ok:
                return error(msg, 400)
            ok, msg = validate_days(days)
            if not ok:
                return error(msg, 400)

            engine = BatchBacktestEngine(
                market, days=int(days), lot_size=lot_size, sl_pct=sl_pct, tgt_pct=tgt_pct,
                trailing_sl_pct=trailing_sl_pct, greeks_filter=greeks_filter,
            )
            jobs = engine.build_synthetic_jobs(strategies, symbols, resolutions)

        if not jobs:
            return error("No valid job combinations to run", 400)

        result = engine.run_and_rank(jobs, rank_by=rank_by)
        return jsonify({"success": True, **result})

    except Exception as e:
        logger.error(f"Batch backtest error: {e}")
        return error(str(e), 400)
