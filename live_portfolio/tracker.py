"""Live Portfolio Tracker — real-time P&L and position management."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_buy_price: float
    current_price: float = 0.0
    last_updated: datetime = field(default_factory=datetime.utcnow)

    @property
    def invested(self) -> float:
        return self.quantity * self.avg_buy_price

    @property
    def current_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealised_pnl(self) -> float:
        return self.current_value - self.invested

    @property
    def pnl_pct(self) -> float:
        if self.invested == 0:
            return 0.0
        return (self.unrealised_pnl / self.invested) * 100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_buy_price": self.avg_buy_price,
            "current_price": self.current_price,
            "invested": round(self.invested, 2),
            "current_value": round(self.current_value, 2),
            "unrealised_pnl": round(self.unrealised_pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "last_updated": self.last_updated.isoformat(),
        }


class LivePortfolioTracker:
    """Tracks open positions and streams live P&L updates."""

    def __init__(self, price_feed: Callable[[list[str]], dict[str, float]]):
        """
        price_feed: async callable that accepts a list of symbols and returns
                    a dict {symbol: latest_price}.
        """
        self._price_feed = price_feed
        self._positions: Dict[str, Position] = {}
        self._subscribers: List[Callable[[dict], None]] = []
        self._running = False

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def add_position(self, symbol: str, quantity: float, avg_buy_price: float):
        key = symbol.upper()
        if key in self._positions:
            # Average-down / up
            existing = self._positions[key]
            total_qty = existing.quantity + quantity
            existing.avg_buy_price = (
                (existing.avg_buy_price * existing.quantity + avg_buy_price * quantity)
                / total_qty
            )
            existing.quantity = total_qty
        else:
            self._positions[key] = Position(symbol=key, quantity=quantity, avg_buy_price=avg_buy_price)

    def remove_position(self, symbol: str, quantity: float):
        key = symbol.upper()
        if key not in self._positions:
            raise KeyError(f"No open position for {key}")
        pos = self._positions[key]
        if quantity >= pos.quantity:
            del self._positions[key]
        else:
            pos.quantity -= quantity

    # ------------------------------------------------------------------
    # Subscribers (push updates to WebSocket clients, etc.)
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[dict], None]):
        self._subscribers.append(callback)

    def _notify(self, snapshot: dict):
        for cb in self._subscribers:
            try:
                cb(snapshot)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Live update loop
    # ------------------------------------------------------------------

    async def start(self, interval_seconds: float = 5.0):
        self._running = True
        while self._running:
            await self._refresh()
            await asyncio.sleep(interval_seconds)

    def stop(self):
        self._running = False

    async def _refresh(self):
        symbols = list(self._positions.keys())
        if not symbols:
            return
        prices = await self._price_feed(symbols)
        now = datetime.utcnow()
        for symbol, price in prices.items():
            if symbol in self._positions:
                self._positions[symbol].current_price = price
                self._positions[symbol].last_updated = now
        self._notify(self.snapshot())

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        positions = [p.to_dict() for p in self._positions.values()]
        total_invested = sum(p.invested for p in self._positions.values())
        total_value = sum(p.current_value for p in self._positions.values())
        return {
            "positions": positions,
            "summary": {
                "total_invested": round(total_invested, 2),
                "total_value": round(total_value, 2),
                "total_pnl": round(total_value - total_invested, 2),
                "total_pnl_pct": round(
                    ((total_value - total_invested) / total_invested * 100)
                    if total_invested
                    else 0,
                    2,
                ),
            },
            "as_of": datetime.utcnow().isoformat(),
        }
