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
sees the data, from the real traded LTP — everything else (bid/ask/volume/
OI/OI-change) comes straight from Fyers and is just persisted here.

REAL-DATA-ONLY POLICY:
save_snapshot() only ever persists a row when the caller's chain_result is
explicitly success=True AND mock=False — anything else (a failed API call,
missing data, or a reconstructed/mock-shaped payload) is treated as
"unavailable" and is never written. There is no synthetic/fallback
generator anywhere in this module. All read functions additionally filter
out any legacy mock=1 rows still sitting in the database from before this
policy existed (see "Legacy mock-data quarantine" below) — those rows are
never deleted automatically, just excluded from being served.

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
                quarantined   INTEGER NOT NULL DEFAULT 0,
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
        if "quarantined" not in existing:
            try:
                c.execute("ALTER TABLE snapshots ADD COLUMN quarantined INTEGER NOT NULL DEFAULT 0")
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
# Normalize a live Fyers option-chain response into strike-keyed CE/PE rows.
#
# Only the real Fyers shape is handled here:
#   data.optionsChain = [{strike_price, option_type, ltp, bid, ask, oi,
#                          oich, volume, iv, delta, ...}]
#   (Greeks/iv are added onto these rows upstream by
#   MarketDataService._enrich_with_greeks(), computed from the real traded
#   LTP — never synthetic.)
#
# The old "mock/reconstructed" shape (data.expiryData rows carrying a
# "strike" key directly, as produced by the Black-Scholes reconstruction
# endpoint or the now-removed mock chain generator) is intentionally NOT
# handled here — such a payload can never be archived, satisfying the
# REAL-DATA-ONLY policy structurally rather than just by convention.
# ---------------------------------------------------------------------------

# Our internal field name -> the live Fyers response key
_FIELD_MAP = {
    "ltp"      : "ltp",
    "bid"      : "bid",
    "ask"      : "ask",
    "oi"       : "oi",
    "oi_change": "oich",
    "volume"   : "volume",
    "iv"       : "iv",
    "delta"    : "delta",
    "gamma"    : "gamma",
    "theta"    : "theta",
    "vega"     : "vega",
}


def _normalize_rows(chain_result: dict) -> tuple[list[dict], float]:
    data = chain_result.get("data", {})

    options_chain = data.get("optionsChain", [])
    if not options_chain:
        return [], 0.0

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
        for field, live_key in _FIELD_MAP.items():
            if ce.get(live_key) is not None:
                row[f"ce_{field}"] = ce[live_key]
            if pe.get(live_key) is not None:
                row[f"pe_{field}"] = pe[live_key]
        rows.append(row)
    return rows, spot


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_snapshot(symbol: str, expiry_date: str, chain_result: dict) -> bool:
    """
    Save one option-chain snapshot for `symbol`'s `expiry_date` contract,
    if the market is open. expiry_date must be YYYY-MM-DD (use
    parse_expiry_to_date() to convert a raw Fyers expiry value first).

    REAL-DATA-ONLY gate: chain_result must be explicitly success=True and
    mock=False, or nothing is written — a failed/unavailable API call never
    falls back to writing a placeholder/mock row; it's simply not saved.
    Returns True if a snapshot was written.
    """
    try:
        if not _within_market_hours():
            return False

        if chain_result.get("success") is not True:
            return False
        if chain_result.get("mock") is not False:
            return False

        rows, spot = _normalize_rows(chain_result)
        if not rows or not spot:
            return False

        step = 100 if spot < 30000 else 200
        atm  = round(spot / step) * step

        captured_at = int(time.time())
        capture_date = _today_str()

        side_cols = [f"{side}_{f}" for f in _SIDE_COLUMNS for side in ("ce", "pe")]
        col_list  = ", ".join(["symbol", "expiry_date", "capture_date", "captured_at", "spot", "mock",
                                "days_to_expiry_used", "strike"] + side_cols + ["atm"])
        placeholders = ", ".join(["?"] * (8 + len(side_cols) + 1))

        values = []
        for r in rows:
            row_vals = [
                # mock is hardcoded 0 here — the gate above already requires
                # chain_result["mock"] is False before we reach this point,
                # so every row this function ever inserts is real by construction.
                symbol, expiry_date, capture_date, captured_at, spot, 0, DEFAULT_DAYS_TO_EXPIRY,
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
#
# Every read function below filters WHERE mock=0 — legacy mock=1 rows still
# physically present in the database (written before the REAL-DATA-ONLY
# policy existed) are excluded from every result the app can see, without
# being deleted. See "Legacy mock-data quarantine" further below for how to
# identify/flag those rows explicitly.
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
    expiry contract. Only ever considers real (mock=0) rows.
    """
    with _conn() as c:
        if target_epoch is None:
            row = c.execute("""
                SELECT MAX(captured_at) AS ts FROM snapshots
                WHERE symbol=? AND expiry_date=? AND capture_date=? AND mock=0
            """, (symbol, expiry_date, capture_date)).fetchone()
        else:
            row = c.execute("""
                SELECT captured_at AS ts FROM snapshots
                WHERE symbol=? AND expiry_date=? AND capture_date=? AND mock=0
                ORDER BY ABS(captured_at - ?) LIMIT 1
            """, (symbol, expiry_date, capture_date, target_epoch)).fetchone()

        if not row or row["ts"] is None:
            return None

        db_rows = c.execute("""
            SELECT * FROM snapshots
            WHERE symbol=? AND expiry_date=? AND capture_date=? AND captured_at=? AND mock=0
            ORDER BY strike
        """, (symbol, expiry_date, capture_date, row["ts"])).fetchall()

        return _rows_to_snapshot(db_rows) or None


def list_expiries(symbol: str) -> list[str]:
    """Return sorted list of expiry dates (YYYY-MM-DD) that have any archived REAL data."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT expiry_date FROM snapshots WHERE symbol=? AND mock=0 ORDER BY expiry_date", (symbol,)
        ).fetchall()
    return [r["expiry_date"] for r in rows]


def list_available_dates(symbol: str, expiry_date: str | None = None) -> list[str]:
    """
    Return sorted list of capture dates (YYYY-MM-DD) that have saved REAL
    data. If expiry_date is given, restrict to that expiry; otherwise union
    across all expiries archived for this symbol.
    """
    with _conn() as c:
        if expiry_date:
            rows = c.execute("""
                SELECT DISTINCT capture_date FROM snapshots
                WHERE symbol=? AND expiry_date=? AND mock=0 ORDER BY capture_date
            """, (symbol, expiry_date)).fetchall()
        else:
            rows = c.execute("""
                SELECT DISTINCT capture_date FROM snapshots
                WHERE symbol=? AND mock=0 ORDER BY capture_date
            """, (symbol,)).fetchall()
    return [r["capture_date"] for r in rows]


def list_expiries_for_capture_date(symbol: str, capture_date: str) -> list[str]:
    """Which expiry dates have a saved REAL snapshot captured on this particular day?"""
    with _conn() as c:
        rows = c.execute("""
            SELECT DISTINCT expiry_date FROM snapshots
            WHERE symbol=? AND capture_date=? AND mock=0 ORDER BY expiry_date
        """, (symbol, capture_date)).fetchall()
    return [r["expiry_date"] for r in rows]


def list_snapshot_times(symbol: str, expiry_date: str, capture_date: str) -> list[int]:
    """
    Return sorted list of captured_at unix timestamps for this expiry+capture
    date — used to step forward/backward through the day's snapshots for
    replay / walk-forward backtesting in the Simulator. Real (mock=0) only.
    """
    with _conn() as c:
        rows = c.execute("""
            SELECT DISTINCT captured_at FROM snapshots
            WHERE symbol=? AND expiry_date=? AND capture_date=? AND mock=0
            ORDER BY captured_at
        """, (symbol, expiry_date, capture_date)).fetchall()
    return [r["captured_at"] for r in rows]


def list_snapshots_range(symbol: str, expiry_date: str, from_epoch: int, to_epoch: int | None = None) -> list[dict]:
    """
    Return every archived REAL snapshot for this expiry contract between
    from_epoch and to_epoch (inclusive), across however many capture dates
    that spans, ordered by time. Used by the walk-forward backtest engine
    to replay a trade's entire holding period using real captured LTPs
    (not a Black-Scholes simulation).
    """
    with _conn() as c:
        if to_epoch is None:
            rows = c.execute("""
                SELECT DISTINCT captured_at FROM snapshots
                WHERE symbol=? AND expiry_date=? AND captured_at >= ? AND mock=0
                ORDER BY captured_at
            """, (symbol, expiry_date, from_epoch)).fetchall()
        else:
            rows = c.execute("""
                SELECT DISTINCT captured_at FROM snapshots
                WHERE symbol=? AND expiry_date=? AND captured_at BETWEEN ? AND ? AND mock=0
                ORDER BY captured_at
            """, (symbol, expiry_date, from_epoch, to_epoch)).fetchall()
        times = [r["captured_at"] for r in rows]

        snapshots = []
        for t in times:
            db_rows = c.execute("""
                SELECT * FROM snapshots
                WHERE symbol=? AND expiry_date=? AND captured_at=? AND mock=0
                ORDER BY strike
            """, (symbol, expiry_date, t)).fetchall()
            snap = _rows_to_snapshot(db_rows)
            if snap:
                snapshots.append(snap)
        return snapshots


def db_stats() -> dict:
    """Quick diagnostics: row counts (total/real/mock/quarantined) and file size."""
    size_bytes = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    with _conn() as c:
        total       = c.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"]
        real_rows   = c.execute("SELECT COUNT(*) AS n FROM snapshots WHERE mock=0").fetchone()["n"]
        mock_rows   = c.execute("SELECT COUNT(*) AS n FROM snapshots WHERE mock=1").fetchone()["n"]
        quarantined = c.execute("SELECT COUNT(*) AS n FROM snapshots WHERE quarantined=1").fetchone()["n"]
    return {
        "rows": total, "real_rows": real_rows, "mock_rows": mock_rows, "quarantined_rows": quarantined,
        "size_bytes": size_bytes, "size_mb": round(size_bytes / 1_000_000, 2), "path": DB_PATH,
    }


# ---------------------------------------------------------------------------
# Legacy mock-data quarantine
#
# Neither function here ever deletes a row or runs automatically — both
# must be explicitly invoked (via the /api/optionchain/archive/mock-audit
# and /api/optionchain/archive/quarantine-mock routes). Rows are already
# excluded from every read function above purely by mock=0 filtering, so
# quarantining doesn't change what the app serves — it's an explicit,
# reversible audit-trail marker for whoever later decides whether to
# physically remove these rows.
# ---------------------------------------------------------------------------

def mock_audit_summary(symbol: str | None = None) -> list[dict]:
    """
    Read-only: counts of legacy mock=1 rows currently in the database,
    grouped by symbol + expiry_date, so they can be identified before any
    decision is made about them. Makes no changes.
    """
    with _conn() as c:
        if symbol:
            rows = c.execute("""
                SELECT symbol, expiry_date, quarantined, COUNT(*) AS n
                FROM snapshots WHERE mock=1 AND symbol=?
                GROUP BY symbol, expiry_date, quarantined
                ORDER BY symbol, expiry_date
            """, (symbol,)).fetchall()
        else:
            rows = c.execute("""
                SELECT symbol, expiry_date, quarantined, COUNT(*) AS n
                FROM snapshots WHERE mock=1
                GROUP BY symbol, expiry_date, quarantined
                ORDER BY symbol, expiry_date
            """).fetchall()
    return [{"symbol": r["symbol"], "expiry_date": r["expiry_date"],
             "quarantined": bool(r["quarantined"]), "rows": r["n"]} for r in rows]


def quarantine_stale_mock_rows() -> int:
    """
    Marks every currently mock=1, not-yet-quarantined row as quarantined=1.
    Never deletes anything. Never called automatically anywhere in this
    codebase — only reachable via an explicit POST to
    /api/optionchain/archive/quarantine-mock. Returns the number of rows
    flagged.
    """
    with _conn() as c:
        cur = c.execute("UPDATE snapshots SET quarantined=1 WHERE mock=1 AND quarantined=0")
        return cur.rowcount
