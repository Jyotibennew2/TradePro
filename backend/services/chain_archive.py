"""
TradePro Backend - Option Chain Archive
Saves real (live) option-chain snapshots to disk every few minutes so that,
over time, TradePro builds its own genuine historical option-chain database —
no dependency on NSE's fragile/changing bhavcopy format.

Also computes and stores IV + Greeks (delta/gamma/theta/vega) for every
saved strike, backed out from the real traded LTP via the existing
Black-Scholes/IV-solver engine — so archived snapshots are immediately
usable for Greeks-aware backtesting later, not just raw LTP/OI.

Storage layout (JSON Lines, one snapshot per line, easy to append + stream):
    data/archive/<SYMBOL>/<YYYY-MM-DD>.jsonl

Each line:
    {"t": 1752999999, "spot": 24312.5, "mock": false,
     "rows": [{"strike":24300,"ce_ltp":180.2,"pe_ltp":142.1,
               "ce_oi":912000,"pe_oi":845000,
               "ce_iv":14.8,"pe_iv":15.1,
               "ce_delta":0.52,"pe_delta":-0.48,
               "ce_gamma":0.0012,"pe_gamma":0.0012,
               "ce_theta":-12.4,"pe_theta":-11.9,
               "ce_vega":9.8,"pe_vega":9.8,
               "atm":true}, ...]}

Compatible with Python 3.11+, Termux, Linux. Stdlib only for I/O.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta

from backend.greeks import GreeksEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ARCHIVE_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "data", "archive")
ARCHIVE_ROOT = os.path.abspath(ARCHIVE_ROOT)

IST = timezone(timedelta(hours=5, minutes=30))

# Assumed time-to-expiry for Greeks/IV back-out when the live feed doesn't
# tell us the exact contract expiry. Nifty/BankNifty weekly options are the
# most heavily traded, so 7 calendar days is a reasonable default.
DEFAULT_DAYS_TO_EXPIRY = 7
RISK_FREE_RATE         = 0.065


def _symbol_dir(symbol: str) -> str:
    d = os.path.join(ARCHIVE_ROOT, symbol.upper())
    os.makedirs(d, exist_ok=True)
    return d


def _today_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _file_for(symbol: str, date_str: str) -> str:
    return os.path.join(_symbol_dir(symbol), f"{date_str}.jsonl")


def _within_market_hours() -> bool:
    """True roughly 9:15–15:30 IST on a weekday — avoids saving junk outside trading hours."""
    now = datetime.now(IST)
    if now.weekday() >= 5:   # Sat/Sun
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= minutes <= (15 * 60 + 30)


# ---------------------------------------------------------------------------
# Normalize whichever chain shape we were given into strike-keyed CE/PE data
# ---------------------------------------------------------------------------

def _normalize_rows(chain_result: dict) -> tuple[list[dict], float]:
    """
    MarketDataService.get_option_chain() can return two different shapes:
      - mock/reconstructed : data.expiryData = [{strike, ce_ltp, pe_ltp, ce_oi, pe_oi, atm}, ...]
      - live Fyers          : data.optionsChain = [{strike_price, option_type, ltp, oi}, ...]
                               (plus one row with option_type "" carrying the spot LTP)
    Returns (rows, spot) in the normalized {strike, ce_ltp, pe_ltp, ce_oi, pe_oi} shape.
    """
    data = chain_result.get("data", {})

    if data.get("expiryData") and isinstance(data["expiryData"], list) and data["expiryData"] and "strike" in data["expiryData"][0]:
        rows = [{
            "strike": r.get("strike"),
            "ce_ltp": r.get("ce_ltp"),
            "pe_ltp": r.get("pe_ltp"),
            "ce_oi" : r.get("ce_oi"),
            "pe_oi" : r.get("pe_oi"),
        } for r in data["expiryData"]]
        spot = chain_result.get("spot", 0) or 0
        return rows, spot

    options_chain = data.get("optionsChain", [])
    if options_chain:
        ce_map: dict[float, dict] = {}
        pe_map: dict[float, dict] = {}
        spot = 0.0
        for item in options_chain:
            if item.get("option_type", "") == "":
                spot = item.get("ltp", 0) or spot
                continue
            strike = item.get("strike_price")
            if strike is None:
                continue
            target = ce_map if item.get("option_type") == "CE" else pe_map
            target[strike] = item

        strikes = sorted(set(list(ce_map.keys()) + list(pe_map.keys())))
        rows = [{
            "strike": k,
            "ce_ltp": ce_map.get(k, {}).get("ltp"),
            "pe_ltp": pe_map.get(k, {}).get("ltp"),
            "ce_oi" : ce_map.get(k, {}).get("oi"),
            "pe_oi" : pe_map.get(k, {}).get("oi"),
        } for k in strikes]
        return rows, spot

    return [], 0.0


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_snapshot(symbol: str, chain_result: dict) -> bool:
    """
    Save one option-chain snapshot for `symbol` if the market is open.
    Backs out IV + Greeks for each strike from the real LTP.
    Returns True if a snapshot was written.
    """
    try:
        if not _within_market_hours():
            return False

        rows, spot = _normalize_rows(chain_result)
        if not rows or not spot:
            return False

        step = 100 if spot < 30000 else 200
        atm  = round(spot / step) * step
        T    = DEFAULT_DAYS_TO_EXPIRY / 365

        enriched = []
        for r in rows:
            strike = r["strike"]
            if strike is None:
                continue

            row = {
                "strike": strike,
                "ce_ltp": r.get("ce_ltp"),
                "pe_ltp": r.get("pe_ltp"),
                "ce_oi" : r.get("ce_oi"),
                "pe_oi" : r.get("pe_oi"),
                "atm"   : strike == atm,
            }

            if r.get("ce_ltp"):
                g = GreeksEngine.calculate(spot, strike, T, RISK_FREE_RATE, 0.15, "call", market_price=r["ce_ltp"])
                row.update({"ce_iv": g.iv, "ce_delta": g.delta, "ce_gamma": g.gamma, "ce_theta": g.theta, "ce_vega": g.vega})

            if r.get("pe_ltp"):
                g = GreeksEngine.calculate(spot, strike, T, RISK_FREE_RATE, 0.15, "put", market_price=r["pe_ltp"])
                row.update({"pe_iv": g.iv, "pe_delta": g.delta, "pe_gamma": g.gamma, "pe_theta": g.theta, "pe_vega": g.vega})

            enriched.append(row)

        snapshot = {
            "t"                : int(time.time()),
            "spot"              : spot,
            "mock"              : bool(chain_result.get("mock", True)),
            "days_to_expiry_used": DEFAULT_DAYS_TO_EXPIRY,
            "rows"              : enriched,
        }

        path = _file_for(symbol, _today_str())
        with open(path, "a") as f:
            f.write(json.dumps(snapshot) + "\n")
        return True
    except Exception as e:
        logger.warning(f"chain_archive.save_snapshot({symbol}) failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def load_day(symbol: str, date_str: str) -> list[dict]:
    """Return all saved snapshots for a given date (YYYY-MM-DD), oldest first."""
    path = _file_for(symbol, date_str)
    if not os.path.exists(path):
        return []
    snapshots = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return snapshots


def list_available_dates(symbol: str) -> list[str]:
    """Return sorted list of dates (YYYY-MM-DD) that have at least one saved snapshot."""
    d = _symbol_dir(symbol)
    if not os.path.isdir(d):
        return []
    dates = [f[:-6] for f in os.listdir(d) if f.endswith(".jsonl")]
    return sorted(dates)


def nearest_snapshot(symbol: str, date_str: str, target_epoch: int | None = None) -> dict | None:
    """
    Return the snapshot for a date closest to target_epoch (defaults to EOD/last snapshot
    of that day — i.e. closing chain).
    """
    snaps = load_day(symbol, date_str)
    if not snaps:
        return None
    if target_epoch is None:
        return snaps[-1]   # last snapshot of the day ≈ closing chain
    return min(snaps, key=lambda s: abs(s["t"] - target_epoch))
