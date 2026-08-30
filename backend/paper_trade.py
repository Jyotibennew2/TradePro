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
from backend.risk import LOT_SIZES, risk_manager

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

    Instrument-agnostic: `option_type`/`strike`/`expiry` are purely
    descriptive metadata (used for display only) — the P&L math below
    never reads them, so options (NIFTY/BANKNIFTY/MIDCPNIFTY) and equity
    (e.g. "NSE:RELIANCE-EQ", option_type="EQ", strike=0, expiry="") flow
    through the exact same place/exit/modify/MTM logic.

    Risk gating (Phase 3): place_order() now consults the existing
    `risk_manager` singleton (backend/risk.py) — a fully-built
    RiskManager class that was never wired into this engine before —
    for max open trades and daily loss limit, in addition to the
    margin check that already existed here. remove_trade() is called
    on every exit path (manual exit and SL/Target auto-exit) so the
    open-trade counter always reflects reality.

    "Daily" loss is approximated as loss for the current in-memory
    session (this engine has no persistent per-calendar-day ledger —
    it resets whenever the server restarts, same as everything else
    in this class). Documented here rather than silently treated as
    a true calendar-day limit.
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
        # LOT_SIZES only has NIFTY/BANKNIFTY/MIDCPNIFTY entries (options).
        # Any symbol not in there — i.e. an equity symbol like
        # "NSE:RELIANCE-EQ" — is 1 unit = 1 share, so the fallback here is
        # 1, not the old 50 (which silently multiplied every equity qty by
        # an options lot size). Explicit LOT_SIZES entries are untouched.
        lot_size    = LOT_SIZES.get(symbol.upper(), 1)
        total_qty   = qty * lot_size
        margin_req  = entry_price * total_qty

        if margin_req > (self.capital - self.used_margin):
            logger.warning(f"Insufficient margin: required={margin_req} available={self.capital - self.used_margin}")
            return {"success": False, "error": "Insufficient margin"}

        # Risk gate 1: daily (session) loss limit. risk_manager.capital is
        # kept in sync with this engine's live capital so the limit is
        # always computed off the real paper account size, not a stale
        # default.
        risk_manager.update_capital(self.capital)
        session_loss = max(0.0, -sum(o.pnl for o in self._history))
        daily = risk_manager.check_daily_loss(session_loss)
        if daily.limit_hit:
            logger.warning(f"Daily loss limit hit: loss={daily.daily_loss} limit={daily.limit}")
            return {"success": False, "error": f"Daily loss limit reached (₹{daily.daily_loss}/₹{daily.limit}) — no new trades allowed this session"}

        # Risk gate 2: max concurrent open trades.
        if not risk_manager.add_trade():
            return {"success": False, "error": f"Max open trades reached ({risk_manager.max_trades}) — close a position before opening another"}

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
        risk_manager.remove_trade()

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
        risk_manager.remove_trade()
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
            "risk"           : {
                "max_trades"      : risk_manager.max_trades,
                "daily_loss_limit": round(self.capital * risk_manager.daily_loss_limit, 2),
                "session_loss"    : round(max(0.0, -realized_pnl), 2),
            },
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
        # Bring the risk manager's open-trade counter back to zero too —
        # otherwise a reset paper account would still be gated by
        # trades that no longer exist.
        while risk_manager._open_trades > 0:
            risk_manager.remove_trade()
        risk_manager.update_capital(capital)
        logger.info(f"Paper trading reset: capital={capital}")
        return {"success": True, "capital": capital}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

paper_engine = PaperTradeEngine()
