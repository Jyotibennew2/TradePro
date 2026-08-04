"""
TradePro Backend - Paper Trading Engine
Virtual trading with P&L tracking.
Compatible with Python 3.11+, Termux, Linux.
"""

import time
import uuid
import logging
from dataclasses import dataclass, asdict, field
from typing import Optional
from backend.risk import LOT_SIZES
from backend import paper_trade_store as store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INITIAL_CAPITAL : float = 500000.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PaperOrder:
    order_id    : str
    symbol      : str
    option_type : str
    strike      : float
    expiry      : str
    action      : str       # BUY or SELL
    qty         : int
    entry_price : float
    exit_price  : float     = 0.0
    sl          : float     = 0.0
    target      : float     = 0.0
    status      : str       = "OPEN"   # OPEN / CLOSED / SL_HIT / TARGET_HIT
    entry_time  : float     = field(default_factory=time.time)
    exit_time   : float     = 0.0
    pnl         : float     = 0.0
    mtm         : float     = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        # Keep raw unix-epoch fields alongside the formatted display strings -
        # the frontend needs a real numeric timestamp to build an accurate
        # equity curve (Dashboard's chart was previously fabricating fake
        # interpolated points instead of plotting real trade history because
        # only a pre-formatted string was available, not a sortable/plottable
        # timestamp).
        d["entry_time_epoch"] = self.entry_time
        d["exit_time_epoch"]  = self.exit_time
        d["entry_time"] = time.strftime("%d %b %H:%M:%S", time.localtime(self.entry_time))
        d["exit_time"]  = time.strftime("%d %b %H:%M:%S", time.localtime(self.exit_time)) if self.exit_time else ""
        return d

    @classmethod
    def from_store_row(cls, row: dict) -> "PaperOrder":
        """Reconstruct a PaperOrder from a raw SQLite row (see paper_trade_store)."""
        return cls(
            order_id    = row["order_id"],
            symbol      = row["symbol"],
            option_type = row["option_type"],
            strike      = row["strike"],
            expiry      = row["expiry"] or "",
            action      = row["action"],
            qty         = row["qty"],
            entry_price = row["entry_price"],
            exit_price  = row["exit_price"],
            sl          = row["sl"],
            target      = row["target"],
            status      = row["status"],
            entry_time  = row["entry_time"],
            exit_time   = row["exit_time"],
            pnl         = row["pnl"],
            mtm         = row["mtm"],
        )


# ---------------------------------------------------------------------------
# Paper Trading Engine
# ---------------------------------------------------------------------------

class PaperTradeEngine:
    """
    Paper trading engine. In-memory dicts (self._orders, self._history) are
    the hot path for all reads - unchanged in shape/behavior from before.
    Every state-changing call (place/exit/modify/auto-exit/reset) now also
    writes through to SQLite (backend/paper_trade_store.py), and __init__
    loads existing state back from there - so a server restart no longer
    wipes open positions or trade history (previously it silently did,
    every single restart).
    """

    def __init__(self, capital: float = INITIAL_CAPITAL) -> None:
        self.capital       : float                  = store.load_capital(capital)
        self._orders       : dict[str, PaperOrder]  = {
            row["order_id"]: PaperOrder.from_store_row(row) for row in store.load_open_orders()
        }
        self._history      : list[PaperOrder]       = [
            PaperOrder.from_store_row(row) for row in store.load_history()
        ]
        # used_margin isn't persisted directly - it's fully recoverable as
        # the sum of entry_price*qty across currently-open orders, which
        # avoids a second source of truth that could drift out of sync.
        self.used_margin   : float = sum(o.entry_price * o.qty for o in self._orders.values())
        if self._orders or self._history:
            logger.info(f"Paper trade state restored from disk: {len(self._orders)} open, {len(self._history)} closed")

    # ------------------------------------------------------------------
    # Place order
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol      : str,
        option_type : str,
        strike      : float,
        expiry      : str,
        action      : str,
        qty         : int,
        entry_price : float,
        sl          : float = 0.0,
        target      : float = 0.0,
    ) -> dict:
        """Place a paper trade order."""
        lot_size    = LOT_SIZES.get(symbol.upper(), 50)
        total_qty   = qty * lot_size
        margin_req  = entry_price * total_qty

        if margin_req > (self.capital - self.used_margin):
            logger.warning(f"Insufficient margin: required={margin_req} available={self.capital - self.used_margin}")
            return {"success": False, "error": "Insufficient margin"}

        order_id = str(uuid.uuid4())[:8].upper()
        order    = PaperOrder(
            order_id    = order_id,
            symbol      = symbol.upper(),
            option_type = option_type.upper(),
            strike      = strike,
            expiry      = expiry,
            action      = action.upper(),
            qty         = total_qty,
            entry_price = entry_price,
            sl          = sl,
            target      = target,
        )
        self._orders[order_id] = order
        self.used_margin      += margin_req
        store.save_order(order.to_dict())
        logger.info(f"Paper order placed: {order_id} {symbol} {strike} {option_type} {action} qty={total_qty} @ {entry_price}")
        return {"success": True, "order_id": order_id, "order": order.to_dict()}

    # ------------------------------------------------------------------
    # Modify order
    # ------------------------------------------------------------------

    def modify_order(
        self,
        order_id: str,
        sl      : Optional[float] = None,
        target  : Optional[float] = None,
    ) -> dict:
        """Modify SL or target of an open order."""
        order = self._orders.get(order_id)
        if not order:
            return {"success": False, "error": f"Order {order_id} not found"}
        if order.status != "OPEN":
            return {"success": False, "error": f"Order {order_id} is {order.status}"}
        if sl is not None:
            order.sl = sl
        if target is not None:
            order.target = target
        store.save_order(order.to_dict())
        logger.info(f"Paper order modified: {order_id} sl={order.sl} target={order.target}")
        return {"success": True, "order": order.to_dict()}

    # ------------------------------------------------------------------
    # Exit order
    # ------------------------------------------------------------------

    def exit_order(self, order_id: str, exit_price: float) -> dict:
        """Exit an open paper trade."""
        order = self._orders.get(order_id)
        if not order:
            return {"success": False, "error": f"Order {order_id} not found"}
        if order.status != "OPEN":
            return {"success": False, "error": f"Order {order_id} already {order.status}"}

        multiplier       = 1 if order.action == "BUY" else -1
        order.exit_price = exit_price
        order.exit_time  = time.time()
        order.pnl        = round(multiplier * (exit_price - order.entry_price) * order.qty, 2)
        order.status     = "CLOSED"
        order.mtm        = order.pnl

        self.used_margin -= order.entry_price * order.qty
        self.used_margin  = max(0.0, self.used_margin)
        self.capital     += order.pnl

        self._history.append(order)
        del self._orders[order_id]
        store.save_order(order.to_dict())   # upsert: same order_id, now status=CLOSED
        store.save_capital(self.capital)

        logger.info(f"Paper order exited: {order_id} exit={exit_price} pnl={order.pnl}")
        return {"success": True, "pnl": order.pnl, "order": order.to_dict()}

    # ------------------------------------------------------------------
    # MTM update
    # ------------------------------------------------------------------

    def update_mtm(self, order_id: str, ltp: float) -> dict:
        """Update mark-to-market P&L for an open position."""
        order = self._orders.get(order_id)
        if not order:
            return {"success": False, "error": "Order not found"}

        multiplier = 1 if order.action == "BUY" else -1
        order.mtm  = round(multiplier * (ltp - order.entry_price) * order.qty, 2)

        # Check SL / Target
        if order.sl > 0:
            if (order.action == "BUY" and ltp <= order.sl) or \
               (order.action == "SELL" and ltp >= order.sl):
                return self._auto_exit(order, ltp, "SL_HIT")

        if order.target > 0:
            if (order.action == "BUY" and ltp >= order.target) or \
               (order.action == "SELL" and ltp <= order.target):
                return self._auto_exit(order, ltp, "TARGET_HIT")

        # Note: mtm changes on still-open orders are NOT persisted on every
        # tick (that would be a write on every price update, far too much
        # I/O for a value that's recomputed live anyway) - only status
        # transitions (place/exit/modify) are written through.
        return {"success": True, "mtm": order.mtm}

    def _auto_exit(self, order: PaperOrder, ltp: float, reason: str) -> dict:
        multiplier       = 1 if order.action == "BUY" else -1
        order.exit_price = ltp
        order.exit_time  = time.time()
        order.pnl        = round(multiplier * (ltp - order.entry_price) * order.qty, 2)
        order.status     = reason
        order.mtm        = order.pnl
        self.capital    += order.pnl
        self.used_margin = max(0.0, self.used_margin - order.entry_price * order.qty)
        self._history.append(order)
        del self._orders[order.order_id]
        store.save_order(order.to_dict())
        store.save_capital(self.capital)
        logger.info(f"Auto exit [{reason}]: {order.order_id} pnl={order.pnl}")
        return {"success": True, "reason": reason, "pnl": order.pnl, "order": order.to_dict()}

    # ------------------------------------------------------------------
    # Portfolio summary
    # ------------------------------------------------------------------

    def portfolio(self) -> dict:
        """Return current portfolio state."""
        open_positions  = [o.to_dict() for o in self._orders.values()]
        total_mtm       = sum(o.mtm for o in self._orders.values())
        realized_pnl    = sum(o.pnl for o in self._history)
        return {
            "capital"        : round(self.capital, 2),
            "used_margin"    : round(self.used_margin, 2),
            "available"      : round(self.capital - self.used_margin, 2),
            "open_positions" : open_positions,
            "open_count"     : len(open_positions),
            "unrealized_pnl" : round(total_mtm, 2),
            "realized_pnl"   : round(realized_pnl, 2),
            "total_pnl"      : round(total_mtm + realized_pnl, 2),
        }

    # ------------------------------------------------------------------
    # Trade history
    # ------------------------------------------------------------------

    def history(self, limit: int = 50) -> list[dict]:
        """Return last N closed trades."""
        return [o.to_dict() for o in self._history[-limit:]]

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self, capital: float = INITIAL_CAPITAL) -> dict:
        """Reset paper trading account."""
        self.capital     = capital
        self.used_margin = 0.0
        self._orders     = {}
        self._history    = []
        store.clear_all()
        store.save_capital(capital)
        logger.info(f"Paper trading reset: capital={capital}")
        return {"success": True, "capital": capital}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

paper_engine = PaperTradeEngine()
