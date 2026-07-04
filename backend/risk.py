"""
TradePro Backend - Risk Manager
Position sizing, margin check, daily loss limit.
Compatible with Python 3.11+, Termux, Linux.
"""

import logging
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RISK_PER_TRADE : float = 0.02   # 2% of capital per trade
DEFAULT_MAX_TRADES     : int   = 5      # max open trades at once
DEFAULT_DAILY_LOSS_PCT : float = 0.05   # 5% daily loss limit
DEFAULT_LOT_SIZE       : int   = 50     # NIFTY lot size

LOT_SIZES: dict[str, int] = {
    "NIFTY"     : 50,
    "BANKNIFTY" : 15,
    "MIDCPNIFTY": 75,
}


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class RiskResult:
    allowed        : bool
    reason         : str
    max_risk       : float
    position_size  : int
    lot_size       : int
    lots           : int
    margin_required: float
    risk_reward    : float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DailyLimitResult:
    limit_hit   : bool
    daily_loss  : float
    limit       : float
    remaining   : float

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Risk Manager
# ---------------------------------------------------------------------------

class RiskManager:
    """
    Manages position sizing, margin, and daily loss limits.
    All calculations are stateless — pass values in, get result out.
    """

    def __init__(
        self,
        capital          : float = 100000.0,
        risk_per_trade   : float = DEFAULT_RISK_PER_TRADE,
        max_trades       : int   = DEFAULT_MAX_TRADES,
        daily_loss_limit : float = DEFAULT_DAILY_LOSS_PCT,
    ) -> None:
        self.capital          = capital
        self.risk_per_trade   = risk_per_trade
        self.max_trades       = max_trades
        self.daily_loss_limit = daily_loss_limit
        self._daily_loss      = 0.0
        self._open_trades     = 0

    # ------------------------------------------------------------------
    # Max risk per trade
    # ------------------------------------------------------------------

    def max_risk(self) -> float:
        """Maximum loss allowed per trade in rupees."""
        return round(self.capital * self.risk_per_trade, 2)

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def position_size(
        self,
        premium    : float,
        sl_points  : float,
        symbol     : str = "NIFTY",
    ) -> RiskResult:
        """
        Calculate how many lots to trade based on risk.

        Parameters
        ----------
        premium   : Option premium per unit
        sl_points : Stop loss in points (premium units)
        symbol    : Underlying symbol for lot size
        """
        lot_size = LOT_SIZES.get(symbol.upper(), DEFAULT_LOT_SIZE)
        max_risk = self.max_risk()

        if sl_points <= 0:
            return RiskResult(
                allowed=False, reason="SL points must be > 0",
                max_risk=max_risk, position_size=0,
                lot_size=lot_size, lots=0,
                margin_required=0.0, risk_reward=0.0,
            )

        risk_per_lot     = sl_points * lot_size
        lots             = max(1, int(max_risk / risk_per_lot))
        position_size    = lots * lot_size
        margin_required  = round(premium * position_size * 1.1, 2)  # 10% buffer
        risk_reward      = round(premium / sl_points, 2) if sl_points else 0.0

        allowed = (
            self._open_trades < self.max_trades
            and margin_required <= self.capital * 0.5
        )
        reason = (
            "OK" if allowed
            else "Max trades reached" if self._open_trades >= self.max_trades
            else "Insufficient margin"
        )

        logger.info(
            f"Position size: symbol={symbol} lots={lots} "
            f"margin={margin_required} allowed={allowed}"
        )

        return RiskResult(
            allowed         = allowed,
            reason          = reason,
            max_risk        = max_risk,
            position_size   = position_size,
            lot_size        = lot_size,
            lots            = lots,
            margin_required = margin_required,
            risk_reward     = risk_reward,
        )

    # ------------------------------------------------------------------
    # Margin check
    # ------------------------------------------------------------------

    def margin_check(
        self,
        required : float,
        available: float,
    ) -> dict:
        """Check if sufficient margin is available."""
        sufficient = available >= required
        logger.info(f"Margin check: required={required} available={available} ok={sufficient}")
        return {
            "sufficient"  : sufficient,
            "required"    : required,
            "available"   : available,
            "shortfall"   : max(0.0, round(required - available, 2)),
        }

    # ------------------------------------------------------------------
    # Daily loss limit
    # ------------------------------------------------------------------

    def check_daily_loss(self, current_loss: float = 0.0) -> DailyLimitResult:
        """
        Check if daily loss limit has been hit.

        Parameters
        ----------
        current_loss : Total loss today (positive number = loss)
        """
        limit     = round(self.capital * self.daily_loss_limit, 2)
        remaining = round(max(0.0, limit - current_loss), 2)
        hit       = current_loss >= limit

        if hit:
            logger.warning(f"Daily loss limit hit: loss={current_loss} limit={limit}")

        return DailyLimitResult(
            limit_hit  = hit,
            daily_loss = round(current_loss, 2),
            limit      = limit,
            remaining  = remaining,
        )

    # ------------------------------------------------------------------
    # Trade counter
    # ------------------------------------------------------------------

    def add_trade(self) -> bool:
        """Register a new open trade. Returns False if max reached."""
        if self._open_trades >= self.max_trades:
            logger.warning(f"Max trades reached: {self._open_trades}/{self.max_trades}")
            return False
        self._open_trades += 1
        logger.info(f"Trade added: open={self._open_trades}/{self.max_trades}")
        return True

    def remove_trade(self) -> None:
        """Mark a trade as closed."""
        self._open_trades = max(0, self._open_trades - 1)
        logger.info(f"Trade removed: open={self._open_trades}/{self.max_trades}")

    def update_capital(self, new_capital: float) -> None:
        """Update capital (e.g. after funds refresh)."""
        self.capital = new_capital
        logger.info(f"Capital updated: {new_capital}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

risk_manager = RiskManager()
