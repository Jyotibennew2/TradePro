"""
TradePro Backend - Option Chain Archive (SQLite-backed)
Saves real (live) option-chain snapshots so that, over time, TradePro builds
its own genuine historical option-chain database — no dependency on NSE's
fragile/changing bhavcopy format.

STORAGE: a single SQLite file at data/archive/chain_archive.db (stdlib only,
no extra pip install — safe on Termux/ARM where wheels like pyarrow often
fail to build). One row per (symbol, expiry, capture date, snapshot time,
strike) — indexed for fast lookups, much smaller on disk than a JSON-based
layout since field names aren't repeated per row.

Each contract EXPIRY is archived separately (weekly, next-weekly, monthly, ...)
so a user can later pick a specific expiry's chain, not just "whatever was
nearest at capture time".

Every field needed for real backtesting is captured per strike/side:
timestamp, underlying price, expiry, strike, LTP, bid, ask, volume, OI,
change in OI, IV, Delta, Gamma, Theta, Vega. IV + Greeks are computed
upstream by MarketDataService.get_option_chain() before this module ever
sees the data — everything else (bid/ask/volume/OI/OI-change) comes
straight from Fyers (or the mock generator) and is just persisted here.

Compatible with Python 3.11+, Termux, Linux. Stdlib only (sqlite3).
"""

import os
import sqlite3
import time
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "data", "archive")
ARCHIVE_DIR = os.path.abspath(ARCHIVE_DIR)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

DB_PATH = os.path.join(ARCHIVE_DIR, "chain_archive.db")

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_DAYS_TO_EXPIRY = 7   # kept here for reference in the response payload

# Per-side (ce_/pe_) numeric columns stored for every strike
_SIDE_COLUMNS = (
    "ltp", "bid", "ask", "oi", "oi_change", "volume",
    "iv", "delta", "gamma", "theta", "vega",
)


def _conn() -> sqlite3.Connection:
    """
    Open a fresh connection per call — write/read volume here is a few
    thousand rows a day, so connection overhead is negligible and this
    avoids any cross-thread sqlite3 sharing issues between the Flask
    request thread and the background scheduler thread.
    """
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")   # readers don't block the writer
    c.row_factory = sqlite3.Row
    return c


def _init_db() -> None:
    with _conn() as c:
        side_cols_sql = ",\n                ".join(
            f"ce_{f} REAL, pe_{f} REAL" for f in _SIDE_COLUMNS
        )
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT    NOT NULL,
                expiry_date   TEXT    NOT NULL,   -- YYYY-MM-DD, the contract's own expiry
                capture_date  TEXT    NOT NULL,   -- YYYY-MM-DD, day this was captured
                captured_at   INTEGER NOT NULL,   -- unix epoch seconds
                spot          REAL    NOT NULL,
                mock          INTEGER NOT NULL,
                days_to_expiry_used INTEGER,
                strike        REAL    NOT NULL,
                {side_cols_sql},
                atm           INTEGER
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_lookup
            ON snapshots (symbol, expiry_date, capture_date, captured_at)
        """)
        # Migration: add any columns missing from an older DB (safe no-op if already present)
        existing = {row["name"] for row in c.execute("PRAGMA table_info(snapshots)")}
        for f in _SIDE_COLUMNS:
            for side in ("ce", "pe"):
                col = f"{side}_{f}"
                if col not in existing:
                    try:
                        c.execute(f"ALTER TABLE snapshots ADD COLUMN {col} REAL")
                    except sqlite3.OperationalError:
                        pass


_init_db()


def _today_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


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
    string) to a normalized YYYY-MM-DD used as the identifier.
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
# Normalize whichever chain shape we were given into strike-keyed CE/PE data.
# Handles both response shapes:
#   - mock/reconstructed: data.expiryData = [{strike, ce_ltp, ce_bid, ce_ask,
#                                              ce_oi, ce_oich, ce_volume,
#                                              ce_iv, ce_delta, ..., pe_*}]
#   - live Fyers         : data.optionsChain = [{strike_price, option_type,
#                                                 ltp, bid, ask, oi, oich,
#                                                 volume, iv, delta, ...}]
#     (Greeks/iv are added onto the live rows upstream by MarketDataService)
# ---------------------------------------------------------------------------

# Map our internal field name -> (mock key suffix, live Fyers key)
_FIELD_MAP = {
    "ltp"      : ("ltp",   "ltp"),
    "bid"      : ("bid",   "bid"),
    "ask"      : ("ask",   "ask"),
    "oi"       : ("oi",    "oi"),
    "oi_change": ("oich",  "oich"),
    "volume"   : ("volume","volume"),
    "iv"       : ("iv",    "iv"),
    "delta"    : ("delta", "delta"),
    "gamma"    : ("gamma", "gamma"),
    "theta"    : ("theta", "theta"),
    "vega"     : ("vega",  "vega"),
}


def _normalize_rows(chain_result: dict) -> tuple[list[dict], float]:
    data = chain_result.get("data", {})

    expiry_data = data.get("expiryData")
    if expiry_data and isinstance(expiry_data, list) and expiry_data and "strike" in expiry_data[0]:
        rows = []
        for r in expiry_data:
            row = {"strike": r.get("strike")}
            for field, (mock_key, _) in _FIELD_MAP.items():
                if r.get(f"ce_{mock_key}") is not None:
                    row[f"ce_{field}"] = r[f"ce_{mock_key}"]
                if r.get(f"pe_{mock_key}") is not None:
                    row[f"pe_{field}"] = r[f"pe_{mock_key}"]
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
            row = {"strike": k}
            for field, (_, live_key) in _FIELD_MAP.items():
                if ce.get(live_key) is not None:
                    row[f"ce_{field}"] = ce[live_key]
                if pe.get(live_key) is not None:
                    row[f"pe_{field}"] = pe[live_key]
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

        captured_at = int(time.time())
        capture_date = _today_str()
        mock = int(bool(chain_result.get("mock", True)))

        side_cols = [f"{side}_{f}" for f in _SIDE_COLUMNS for side in ("ce", "pe")]
        col_list  = ", ".join(["symbol", "expiry_date", "capture_date", "captured_at", "spot", "mock",
                                "days_to_expiry_used", "strike"] + side_cols + ["atm"])
        placeholders = ", ".join(["?"] * (8 + len(side_cols) + 1))

        values = []
        for r in rows:
            row_vals = [
                symbol, expiry_date, capture_date, captured_at, spot, mock, DEFAULT_DAYS_TO_EXPIRY,
                r.get("strike"),
            ]
            row_vals += [r.get(col) for col in side_cols]
            row_vals.append(int(r.get("strike") == atm))
            values.append(tuple(row_vals))

        with _conn() as c:
            c.executemany(f"INSERT INTO snapshots ({col_list}) VALUES ({placeholders})", values)
        return True
    except Exception as e:
        logger.warning(f"chain_archive.save_snapshot({symbol}, {expiry_date}) failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def _rows_to_snapshot(db_rows: list[sqlite3.Row]) -> dict:
    if not db_rows:
        return {}
    first = db_rows[0]
    out_rows = []
    for r in db_rows:
        row = {"strike": r["strike"], "atm": bool(r["atm"])}
        for f in _SIDE_COLUMNS:
            row[f"ce_{f}"] = r[f"ce_{f}"]
            row[f"pe_{f}"] = r[f"pe_{f}"]
        out_rows.append(row)
    return {
        "t": first["captured_at"], "spot": first["spot"], "mock": bool(first["mock"]),
        "days_to_expiry_used": first["days_to_expiry_used"],
        "rows": out_rows,
    }


def nearest_snapshot(symbol: str, expiry_date: str, capture_date: str, target_epoch: int | None = None) -> dict | None:
    """
    Return the snapshot for a capture date closest to target_epoch (defaults
    to the last snapshot of that day — i.e. the closing chain) for a given
    expiry contract.
    """
    with _conn() as c:
        if target_epoch is None:
            row = c.execute("""
                SELECT MAX(captured_at) AS ts FROM snapshots
                WHERE symbol=? AND expiry_date=? AND capture_date=?
            """, (symbol, expiry_date, capture_date)).fetchone()
        else:
            row = c.execute("""
                SELECT captured_at AS ts FROM snapshots
                WHERE symbol=? AND expiry_date=? AND capture_date=?
                ORDER BY ABS(captured_at - ?) LIMIT 1
            """, (symbol, expiry_date, capture_date, target_epoch)).fetchone()

        if not row or row["ts"] is None:
            return None

        db_rows = c.execute("""
            SELECT * FROM snapshots
            WHERE symbol=? AND expiry_date=? AND capture_date=? AND captured_at=?
            ORDER BY strike
        """, (symbol, expiry_date, capture_date, row["ts"])).fetchall()

        return _rows_to_snapshot(db_rows) or None


def list_expiries(symbol: str) -> list[str]:
    """Return sorted list of expiry dates (YYYY-MM-DD) that have any archived data."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT expiry_date FROM snapshots WHERE symbol=? ORDER BY expiry_date", (symbol,)
        ).fetchall()
    return [r["expiry_date"] for r in rows]


def list_available_dates(symbol: str, expiry_date: str | None = None) -> list[str]:
    """
    Return sorted list of capture dates (YYYY-MM-DD) that have saved data.
    If expiry_date is given, restrict to that expiry; otherwise union
    across all expiries archived for this symbol.
    """
    with _conn() as c:
        if expiry_date:
            rows = c.execute("""
                SELECT DISTINCT capture_date FROM snapshots
                WHERE symbol=? AND expiry_date=? ORDER BY capture_date
            """, (symbol, expiry_date)).fetchall()
        else:
            rows = c.execute("""
                SELECT DISTINCT capture_date FROM snapshots
                WHERE symbol=? ORDER BY capture_date
            """, (symbol,)).fetchall()
    return [r["capture_date"] for r in rows]


def list_expiries_for_capture_date(symbol: str, capture_date: str) -> list[str]:
    """Which expiry dates have a saved snapshot captured on this particular day?"""
    with _conn() as c:
        rows = c.execute("""
            SELECT DISTINCT expiry_date FROM snapshots
            WHERE symbol=? AND capture_date=? ORDER BY expiry_date
        """, (symbol, capture_date)).fetchall()
    return [r["expiry_date"] for r in rows]


def list_snapshot_times(symbol: str, expiry_date: str, capture_date: str) -> list[int]:
    """
    Return sorted list of captured_at unix timestamps for this expiry+capture
    date — used to step forward/backward through the day's snapshots for
    replay / walk-forward backtesting in the Simulator.
    """
    with _conn() as c:
        rows = c.execute("""
            SELECT DISTINCT captured_at FROM snapshots
            WHERE symbol=? AND expiry_date=? AND capture_date=?
            ORDER BY captured_at
        """, (symbol, expiry_date, capture_date)).fetchall()
    return [r["captured_at"] for r in rows]


def db_stats() -> dict:
    """Quick diagnostics: row count and file size — useful for checking storage growth."""
    size_bytes = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    with _conn() as c:
        count = c.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"]
    return {"rows": count, "size_bytes": size_bytes, "size_mb": round(size_bytes / 1_000_000, 2), "path": DB_PATH}
