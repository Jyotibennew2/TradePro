"""
TradePro Backend - Portfolio Module
Current holdings, open positions, realized/unrealized PnL.
Compatible with Python 3.11+, Termux, Linux.
"""

import time
import logging
from dataclasses import dataclass, asdict
from typing import Optional
from backend.paper_trade import paper_engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PositionSummary:
    symbol      : str
    option_type : str
    strike      : float
    expiry      : str
    action      : str
    qty         : int
    entry_price : float
    ltp         : float
    mtm         : float
    pnl_pct     : float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DailyPnL:
    date          : str
    realized_pnl  : float
    unrealized_pnl: float
    total_pnl     : float
    trades        : int
    wins          : int
    losses        : int
    win_rate      : float

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Portfolio Module
# ---------------------------------------------------------------------------

class Portfolio:
    """
    Aggregates paper trading data into portfolio views.
    Provides realized PnL, unrealized PnL, daily PnL.
    """

    def __init__(self) -> None:
        self._daily_snapshot: dict = {}

    # ------------------------------------------------------------------
    # Current holdings
    # ------------------------------------------------------------------

    def current_holdings(self) -> list[dict]:
        """Return all open positions with enriched data."""
        holdings = []
        for order in paper_engine._orders.values():
            pnl_pct = round(
                (order.mtm / (order.entry_price * order.qty)) * 100, 2
            ) if order.entry_price and order.qty else 0.0

            summary = PositionSummary(
                symbol      = order.symbol,
                option_type = order.option_type,
                strike      = order.strike,
                expiry      = order.expiry,
                action      = order.action,
                qty         = order.qty,
                entry_price = order.entry_price,
                ltp         = order.entry_price + (order.mtm / order.qty if order.qty else 0),
                mtm         = order.mtm,
                pnl_pct     = pnl_pct,
            )
            holdings.append(summary.to_dict())
        return holdings

    # ------------------------------------------------------------------
    # Open positions
    # ------------------------------------------------------------------

    def open_positions(self) -> dict:
        """Return open positions summary."""
        holdings       = self.current_holdings()
        total_mtm      = sum(h["mtm"] for h in holdings)
        total_invested = sum(h["entry_price"] * h["qty"] for h in holdings)
        return {
            "positions"      : holdings,
            "count"          : len(holdings),
            "total_mtm"      : round(total_mtm, 2),
            "total_invested" : round(total_invested, 2),
            "pnl_pct"        : round((total_mtm / total_invested * 100), 2) if total_invested else 0.0,
        }

    # ------------------------------------------------------------------
    # Realized PnL
    # ------------------------------------------------------------------

    def realized_pnl(self) -> dict:
        """Return realized PnL from closed trades."""
        history = paper_engine._history
        if not history:
            return {"total": 0.0, "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0}

        total  = sum(o.pnl for o in history)
        wins   = [o for o in history if o.pnl > 0]
        losses = [o for o in history if o.pnl <= 0]

        return {
            "total"    : round(total, 2),
            "trades"   : len(history),
            "wins"     : len(wins),
            "losses"   : len(losses),
            "win_rate" : round(len(wins) / len(history) * 100, 1) if history else 0.0,
            "avg_win"  : round(sum(o.pnl for o in wins)   / len(wins),   2) if wins   else 0.0,
            "avg_loss" : round(sum(o.pnl for o in losses) / len(losses), 2) if losses else 0.0,
        }

    # ------------------------------------------------------------------
    # Unrealized PnL
    # ------------------------------------------------------------------

    def unrealized_pnl(self) -> dict:
        """Return unrealized PnL from open trades."""
        open_orders = list(paper_engine._orders.values())
        total_mtm   = sum(o.mtm for o in open_orders)
        return {
            "total"      : round(total_mtm, 2),
            "positions"  : len(open_orders),
            "breakup"    : [
                {"order_id": o.order_id, "symbol": o.symbol,
                 "strike": o.strike, "mtm": round(o.mtm, 2)}
                for o in open_orders
            ],
        }

    # ------------------------------------------------------------------
    # Daily PnL
    # ------------------------------------------------------------------

    def daily_pnl(self) -> DailyPnL:
        """Return today's PnL summary."""
        today   = time.strftime("%d %b %Y")
        history = paper_engine._history
        realized= sum(o.pnl for o in history)
        unrealized = sum(o.mtm for o in paper_engine._orders.values())
        wins    = [o for o in history if o.pnl > 0]
        losses  = [o for o in history if o.pnl <= 0]

        return DailyPnL(
            date           = today,
            realized_pnl   = round(realized, 2),
            unrealized_pnl = round(unrealized, 2),
            total_pnl      = round(realized + unrealized, 2),
            trades         = len(history),
            wins           = len(wins),
            losses         = len(losses),
            win_rate       = round(len(wins) / len(history) * 100, 1) if history else 0.0,
        )

    # ------------------------------------------------------------------
    # Full summary
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Return complete portfolio summary."""
        port    = paper_engine.portfolio()
        daily   = self.daily_pnl()
        return {
            "capital"        : port["capital"],
            "used_margin"    : port["used_margin"],
            "available"      : port["available"],
            "open_positions" : port["open_count"],
            "unrealized_pnl" : port["unrealized_pnl"],
            "realized_pnl"   : port["realized_pnl"],
            "total_pnl"      : port["total_pnl"],
            "daily"          : daily.to_dict(),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

portfolio = Portfolio()
