"""
TradePro Backend - Paper Trade Persistence (SQLite)

PaperTradeEngine (backend/paper_trade.py) was purely in-memory - every
server restart silently wiped all open positions and closed-trade history.
Given how often this server gets restarted during development (to pick up
new code), this was a real, meaningful data-loss bug, not a hypothetical
one - the paper-trading account effectively reset itself unpredictably.

This module persists every order to SQLite using the same proven pattern
as backend/services/chain_archive.py (WAL mode, one connection per call -
already verified reliable on Termux/ARM under concurrent scheduler +
Flask-request access). PaperTradeEngine loads its state from here on
startup and writes through on every state change; the in-memory dicts
remain the hot path for reads (no behavior/performance change there),
this module is purely the durability layer underneath.

Compatible with Python 3.11+, Termux, Linux. Stdlib only (sqlite3).
"""

import os
import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "archive")
os.makedirs(ARCHIVE_DIR, exist_ok=True)
DB_PATH = os.path.join(ARCHIVE_DIR, "paper_trades.db")

_COLUMNS = (
    "order_id", "symbol", "option_type", "strike", "expiry", "action", "qty",
    "entry_price", "exit_price", "sl", "target", "status",
    "entry_time", "exit_time", "pnl", "mtm",
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.row_factory = sqlite3.Row
    return c


def _init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS paper_orders (
                order_id    TEXT PRIMARY KEY,
                symbol      TEXT NOT NULL,
                option_type TEXT NOT NULL,
                strike      REAL NOT NULL,
                expiry      TEXT,
                action      TEXT NOT NULL,
                qty         INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price  REAL NOT NULL DEFAULT 0,
                sl          REAL NOT NULL DEFAULT 0,
                target      REAL NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'OPEN',
                entry_time  REAL NOT NULL,
                exit_time   REAL NOT NULL DEFAULT 0,
                pnl         REAL NOT NULL DEFAULT 0,
                mtm         REAL NOT NULL DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_orders (status)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS paper_account (
                id      INTEGER PRIMARY KEY CHECK (id = 1),
                capital REAL NOT NULL
            )
        """)


_init_db()


def save_order(order: dict) -> None:
    """Insert or update one order's full row (upsert by order_id)."""
    with _conn() as c:
        placeholders = ", ".join("?" * len(_COLUMNS))
        col_list = ", ".join(_COLUMNS)
        updates = ", ".join(f"{col}=excluded.{col}" for col in _COLUMNS if col != "order_id")
        c.execute(f"""
            INSERT INTO paper_orders ({col_list}) VALUES ({placeholders})
            ON CONFLICT(order_id) DO UPDATE SET {updates}
        """, tuple(order.get(col) for col in _COLUMNS))


def delete_order(order_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM paper_orders WHERE order_id=?", (order_id,))


def load_open_orders() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM paper_orders WHERE status='OPEN'").fetchall()
    return [dict(r) for r in rows]


def load_history(limit: int = 500) -> list[dict]:
    """Closed trades, oldest first (matches PaperTradeEngine._history append order)."""
    with _conn() as c:
        rows = c.execute("""
            SELECT * FROM paper_orders WHERE status != 'OPEN'
            ORDER BY exit_time ASC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_capital(capital: float) -> None:
    with _conn() as c:
        c.execute("""
            INSERT INTO paper_account (id, capital) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET capital=excluded.capital
        """, (capital,))


def load_capital(default: float) -> float:
    with _conn() as c:
        row = c.execute("SELECT capital FROM paper_account WHERE id=1").fetchone()
    return row["capital"] if row else default


def clear_all() -> None:
    """Used by PaperTradeEngine.reset() - wipes all persisted orders and capital."""
    with _conn() as c:
        c.execute("DELETE FROM paper_orders")
        c.execute("DELETE FROM paper_account")
