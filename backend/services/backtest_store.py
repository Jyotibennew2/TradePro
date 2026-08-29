"""
TradePro Backend - Saved Backtest Results (SQLite-backed)

Phase 1 of "Advanced Equity Auto-Trading": lets a user save the result of
ANY backtest they run (Single, Compare, Batch, Batch Real-Data, Walk-Forward)
so it can be reopened later instead of being lost the moment they navigate
away. This module does not run or recompute anything — it only persists
and retrieves the request/response JSON that the existing backtest routes
already produce (backend/routes/backtest.py, backend/batch_backtest.py).

STORAGE: a single SQLite file at data/saved_backtests/saved_backtests.db
(stdlib only) — same approach as backend/services/chain_archive.py, for
the same Termux/ARM-safe reasons (no extra pip wheel to build).

Each row stores the ORIGINAL request parameters and the ORIGINAL result
payload exactly as the frontend received them, as opaque JSON blobs. This
means: no duplicated calculation logic here, and no schema coupling to any
particular backtest engine's result shape — Single/Compare/Batch/Batch
Real-Data/Walk-Forward results can all be saved through the same table
without this module needing to know their internal structure.
"""

import os
import json
import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

STORE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "data", "saved_backtests")
STORE_DIR = os.path.abspath(STORE_DIR)
os.makedirs(STORE_DIR, exist_ok=True)

DB_PATH = os.path.join(STORE_DIR, "saved_backtests.db")

# Matches the "kind" values the frontend already distinguishes (see
# src/pages/Backtest/*.tsx) — validated loosely (not enforced) so a new
# backtest mode added later doesn't require a migration here.
VALID_KINDS = ("single", "compare", "batch", "batch_realdata", "walkforward")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.row_factory = sqlite3.Row
    return c


def _init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS saved_backtests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at   INTEGER NOT NULL,
                label        TEXT,
                kind         TEXT    NOT NULL,
                symbol       TEXT,
                data_source  TEXT,
                request_json TEXT    NOT NULL,
                result_json  TEXT    NOT NULL
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_saved_backtests_created
            ON saved_backtests (created_at DESC)
        """)


_init_db()


def save_backtest(kind: str, request: dict, result: dict,
                   label: str | None = None, symbol: str | None = None,
                   data_source: str | None = None) -> int:
    """
    Persist one backtest run. `request` and `result` are stored verbatim as
    JSON — whatever the frontend already has after calling /api/backtest,
    /api/backtest/batch, or /api/backtest/walkforward. Returns the new row id.
    """
    created_at = int(time.time())
    with _conn() as c:
        cur = c.execute("""
            INSERT INTO saved_backtests (created_at, label, kind, symbol, data_source, request_json, result_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (created_at, label, kind, symbol, data_source, json.dumps(request), json.dumps(result)))
        return cur.lastrowid


def list_backtests(limit: int = 100) -> list[dict]:
    """
    Lightweight list for a history view — deliberately excludes the
    (potentially large) request_json/result_json blobs; use get_backtest()
    to fetch one run's full payload.
    """
    with _conn() as c:
        rows = c.execute("""
            SELECT id, created_at, label, kind, symbol, data_source
            FROM saved_backtests
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_backtest(backtest_id: int) -> dict | None:
    """Full saved run, with request/result parsed back into dicts. None if not found."""
    with _conn() as c:
        row = c.execute("SELECT * FROM saved_backtests WHERE id=?", (backtest_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["request"] = json.loads(out.pop("request_json"))
    except (TypeError, ValueError):
        out["request"] = None
        out.pop("request_json", None)
    try:
        out["result"] = json.loads(out.pop("result_json"))
    except (TypeError, ValueError):
        out["result"] = None
        out.pop("result_json", None)
    return out


def delete_backtest(backtest_id: int) -> bool:
    """Delete one saved run. Returns True if a row was actually removed."""
    with _conn() as c:
        cur = c.execute("DELETE FROM saved_backtests WHERE id=?", (backtest_id,))
        return cur.rowcount > 0
