"""
TradePro Backend - Option Chain Archive
Saves real (live) option-chain snapshots to disk every few minutes so that,
over time, TradePro builds its own genuine historical option-chain database —
no dependency on NSE's fragile/changing bhavcopy format.

Storage layout (JSON Lines, one snapshot per line, easy to append + stream):
    data/archive/<SYMBOL>/<YYYY-MM-DD>.jsonl

Each line:
    {"t": 1752999999, "spot": 24312.5, "mock": false,
     "rows": [{"strike":24300,"ce_ltp":180.2,"pe_ltp":142.1,
               "ce_oi":912000,"pe_oi":845000,"atm":true}, ...]}

Compatible with Python 3.11+, Termux, Linux. Stdlib only — no dependencies.
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
# Save
# ---------------------------------------------------------------------------

def save_snapshot(symbol: str, chain_result: dict) -> bool:
    """
    Save one option-chain snapshot for `symbol` if the market is open.
    `chain_result` is the raw dict returned by MarketDataService.get_option_chain().
    Returns True if a snapshot was written.
    """
    try:
        if not _within_market_hours():
            return False

        rows_raw = chain_result.get("data", {}).get("expiryData", [])
        if not rows_raw:
            return False

        rows = [{
            "strike": r.get("strike"),
            "ce_ltp": r.get("ce_ltp"),
            "pe_ltp": r.get("pe_ltp"),
            "ce_oi" : r.get("ce_oi"),
            "pe_oi" : r.get("pe_oi"),
            "atm"   : r.get("atm", False),
        } for r in rows_raw]

        spot = chain_result.get("spot", 0) or 0
        if not spot and rows:
            atm_row = next((r for r in rows if r["atm"]), rows[len(rows)//2])
            spot = atm_row["strike"]

        snapshot = {
            "t"   : int(time.time()),
            "spot": spot,
            "mock": bool(chain_result.get("mock", True)),
            "rows": rows,
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
