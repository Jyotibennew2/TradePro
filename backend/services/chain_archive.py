"""
TradePro Backend - Option Chain Archive
Saves real (live) option-chain snapshots to disk every few minutes so that,
over time, TradePro builds its own genuine historical option-chain database —
no dependency on NSE's fragile/changing bhavcopy format.

Each contract EXPIRY is archived separately (weekly, next-weekly, monthly, ...)
so a user can later pick a specific expiry's chain, not just "whatever was
nearest at capture time".

IV + Greeks (delta/gamma/theta/vega) are already computed upstream by
MarketDataService.get_option_chain() before this module ever sees the data
— this module just persists whatever it's handed.

Storage layout (JSON Lines, one snapshot per line, easy to append + stream):
    data/archive/<SYMBOL>/exp_<EXPIRY_DATE YYYY-MM-DD>/<CAPTURE_DATE YYYY-MM-DD>.jsonl

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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ARCHIVE_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "data", "archive")
ARCHIVE_ROOT = os.path.abspath(ARCHIVE_ROOT)

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_DAYS_TO_EXPIRY = 7   # kept here for reference in the response payload


def _expiry_dir(symbol: str, expiry_date: str) -> str:
    d = os.path.join(ARCHIVE_ROOT, symbol.upper(), f"exp_{expiry_date}")
    os.makedirs(d, exist_ok=True)
    return d


def _symbol_dir(symbol: str) -> str:
    d = os.path.join(ARCHIVE_ROOT, symbol.upper())
    os.makedirs(d, exist_ok=True)
    return d


def _today_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _file_for(symbol: str, expiry_date: str, capture_date: str) -> str:
    return os.path.join(_expiry_dir(symbol, expiry_date), f"{capture_date}.jsonl")


def _within_market_hours() -> bool:
    """True roughly 9:15–15:30 IST on a weekday — avoids saving junk outside trading hours."""
    now = datetime.now(IST)
    if now.weekday() >= 5:   # Sat/Sun
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= minutes <= (15 * 60 + 30)


def parse_expiry_to_date(expiry_raw: str) -> str:
    """
    Convert a Fyers expiry value (unix timestamp string, or DD-MM-YYYY date
    string) to a normalized YYYY-MM-DD used as the folder name.
    """
    try:
        return datetime.fromtimestamp(int(expiry_raw), IST).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(expiry_raw, "%d-%m-%Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return expiry_raw


# ---------------------------------------------------------------------------
# Normalize whichever chain shape we were given into strike-keyed CE/PE data
# (IV/Greeks, if present, are carried straight through — they were already
# computed by MarketDataService before this snapshot was passed in)
# ---------------------------------------------------------------------------

_GREEK_FIELDS = ("iv", "delta", "gamma", "theta", "vega")


def _normalize_rows(chain_result: dict) -> tuple[list[dict], float]:
    data = chain_result.get("data", {})

    expiry_data = data.get("expiryData")
    if expiry_data and isinstance(expiry_data, list) and expiry_data and "strike" in expiry_data[0]:
        rows = []
        for r in expiry_data:
            row = {"strike": r.get("strike"), "ce_ltp": r.get("ce_ltp"), "pe_ltp": r.get("pe_ltp"),
                   "ce_oi": r.get("ce_oi"), "pe_oi": r.get("pe_oi")}
            for f in _GREEK_FIELDS:
                if r.get(f"ce_{f}") is not None:
                    row[f"ce_{f}"] = r[f"ce_{f}"]
                if r.get(f"pe_{f}") is not None:
                    row[f"pe_{f}"] = r[f"pe_{f}"]
            rows.append(row)
        return rows, chain_result.get("spot", 0) or 0

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
        rows = []
        for k in strikes:
            ce, pe = ce_map.get(k, {}), pe_map.get(k, {})
            row = {"strike": k, "ce_ltp": ce.get("ltp"), "pe_ltp": pe.get("ltp"),
                   "ce_oi": ce.get("oi"), "pe_oi": pe.get("oi")}
            for f in _GREEK_FIELDS:
                if ce.get(f) is not None:
                    row[f"ce_{f}"] = ce[f]
                if pe.get(f) is not None:
                    row[f"pe_{f}"] = pe[f]
            rows.append(row)
        return rows, spot

    return [], 0.0


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_snapshot(symbol: str, expiry_date: str, chain_result: dict) -> bool:
    """
    Save one option-chain snapshot for `symbol`'s `expiry_date` contract,
    if the market is open. expiry_date must be YYYY-MM-DD (use
    parse_expiry_to_date() to convert a raw Fyers expiry value first).
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

        for row in rows:
            row["atm"] = row.get("strike") == atm

        snapshot = {
            "t"                  : int(time.time()),
            "spot"               : spot,
            "mock"               : bool(chain_result.get("mock", True)),
            "days_to_expiry_used": DEFAULT_DAYS_TO_EXPIRY,
            "rows"               : rows,
        }

        path = _file_for(symbol, expiry_date, _today_str())
        with open(path, "a") as f:
            f.write(json.dumps(snapshot) + "\n")
        return True
    except Exception as e:
        logger.warning(f"chain_archive.save_snapshot({symbol}, {expiry_date}) failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def load_day(symbol: str, expiry_date: str, capture_date: str) -> list[dict]:
    """Return all saved snapshots for a given expiry+capture date, oldest first."""
    path = _file_for(symbol, expiry_date, capture_date)
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


def list_expiries(symbol: str) -> list[str]:
    """Return sorted list of expiry dates (YYYY-MM-DD) that have any archived data."""
    d = _symbol_dir(symbol)
    if not os.path.isdir(d):
        return []
    return sorted(f[4:] for f in os.listdir(d) if f.startswith("exp_"))


def list_available_dates(symbol: str, expiry_date: str | None = None) -> list[str]:
    """
    Return sorted list of capture dates (YYYY-MM-DD) that have saved data.
    If expiry_date is given, restrict to that expiry's folder; otherwise
    union across all expiries archived for this symbol.
    """
    if expiry_date:
        d = _expiry_dir(symbol, expiry_date)
        if not os.path.isdir(d):
            return []
        return sorted(f[:-6] for f in os.listdir(d) if f.endswith(".jsonl"))

    all_dates: set[str] = set()
    for exp in list_expiries(symbol):
        all_dates.update(list_available_dates(symbol, exp))
    return sorted(all_dates)


def list_expiries_for_capture_date(symbol: str, capture_date: str) -> list[str]:
    """Which expiry dates have a saved snapshot captured on this particular day?"""
    return [exp for exp in list_expiries(symbol) if capture_date in list_available_dates(symbol, exp)]


def nearest_snapshot(symbol: str, expiry_date: str, capture_date: str, target_epoch: int | None = None) -> dict | None:
    """
    Return the snapshot for a capture date closest to target_epoch (defaults
    to the last snapshot of that day — i.e. the closing chain) for a given
    expiry contract.
    """
    snaps = load_day(symbol, expiry_date, capture_date)
    if not snaps:
        return None
    if target_epoch is None:
        return snaps[-1]
    return min(snaps, key=lambda s: abs(s["t"] - target_epoch))
