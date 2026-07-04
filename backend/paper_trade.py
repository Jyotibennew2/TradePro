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
        d["entry_time"] = time.strftime("%d %b %H:%M:%S", time.localtime(self.entry_time))
        d["exit_time"]  = time.strftime("%d %b %H:%M:%S", time.localtime(self.exit_time)) if self.exit_time else ""
        return d


# ---------------------------------------------------------------------------
# Paper Trading Engine
# ---------------------------------------------------------------------------

class PaperTradeEngine:
    """
    In-memory paper trading engine.
    Supports place, modify, exit, MTM, P&L, history.
    """

    def __init__(self, capital: float = INITIAL_CAPITAL) -> None:
        self.capital       : float                  = capital
        self.used_margin   : float                  = 0.0
        self._orders       : dict[str, PaperOrder]  = {}
        self._history      : list[PaperOrder]       = []

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
        logger.info(f"Paper trading reset: capital={capital}")
        return {"success": True, "capital": capital}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

paper_engine = PaperTradeEngine()
