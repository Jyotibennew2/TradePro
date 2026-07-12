"""
TradePro Backend - Strategy Engine
Options trading strategies with signals, entry, SL, target, RR.
Compatible with Python 3.11+, Termux, Linux.
"""

import logging
from dataclasses import dataclass, asdict
from typing import Optional
from backend.pricing import bs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class StrategyResult:
    strategy    : str
    signal      : str       # BUY / SELL / NEUTRAL
    entry       : float
    sl          : float
    target      : float
    risk_reward : float
    max_profit  : float
    max_loss    : float
    breakeven   : list
    legs        : list
    description : str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Strategy Engine
# ---------------------------------------------------------------------------

class StrategyEngine:
    """
    Generate option strategy signals with entry, SL, target, RR.
    All strategies use Black-Scholes for pricing.
    """

    @staticmethod
    def long_call(S: float, K: float, T: float, r: float, iv: float) -> StrategyResult:
        """Buy ATM/OTM call — bullish view."""
        premium = round(bs(S, K, T, r, iv, "call"), 2)
        sl      = round(premium * 0.5, 2)
        target  = round(premium * 2.0, 2)
        rr      = round(target / sl, 2) if sl else 0

        return StrategyResult(
            strategy    = "Long Call",
            signal      = "BUY",
            entry       = premium,
            sl          = sl,
            target      = target,
            risk_reward = rr,
            max_profit  = None,
            max_loss    = premium,
            breakeven   = [round(K + premium, 2)],
            legs        = [{"action": "BUY", "type": "CE", "strike": K, "premium": premium}],
            description = f"Buy {K} CE @ ₹{premium} | SL ₹{sl} | Target ₹{target}",
        )

    @staticmethod
    def long_put(S: float, K: float, T: float, r: float, iv: float) -> StrategyResult:
        """Buy ATM/OTM put — bearish view."""
        premium = round(bs(S, K, T, r, iv, "put"), 2)
        sl      = round(premium * 0.5, 2)
        target  = round(premium * 2.0, 2)
        rr      = round(target / sl, 2) if sl else 0

        return StrategyResult(
            strategy    = "Long Put",
            signal      = "BUY",
            entry       = premium,
            sl          = sl,
            target      = target,
            risk_reward = rr,
            max_profit  = K - premium,
            max_loss    = premium,
            breakeven   = [round(K - premium, 2)],
            legs        = [{"action": "BUY", "type": "PE", "strike": K, "premium": premium}],
            description = f"Buy {K} PE @ ₹{premium} | SL ₹{sl} | Target ₹{target}",
        )

    @staticmethod
    def short_straddle(S: float, atm: float, T: float, r: float, iv: float) -> StrategyResult:
        """Sell ATM CE + ATM PE — neutral view, low IV expected."""
        ce      = round(bs(S, atm, T, r, iv, "call"), 2)
        pe      = round(bs(S, atm, T, r, iv, "put"),  2)
        premium = round(ce + pe, 2)
        sl      = round(premium * 1.5, 2)
        target  = round(premium * 0.5, 2)
        rr      = round(target / (sl - premium), 2) if sl > premium else 0

        return StrategyResult(
            strategy    = "Short Straddle",
            signal      = "SELL",
            entry       = premium,
            sl          = sl,
            target      = target,
            risk_reward = rr,
            max_profit  = premium,
            max_loss    = None,
            breakeven   = [round(atm - premium, 2), round(atm + premium, 2)],
            legs        = [
                {"action": "SELL", "type": "CE", "strike": atm, "premium": ce},
                {"action": "SELL", "type": "PE", "strike": atm, "premium": pe},
            ],
            description = f"Sell {atm} CE+PE @ ₹{premium} | SL ₹{sl} | Target ₹{target}",
        )

    @staticmethod
    def short_strangle(S: float, atm: float, T: float, r: float, iv: float, width: float = 200) -> StrategyResult:
        """Sell OTM CE + OTM PE — neutral, wider range."""
        ce_strike = atm + width
        pe_strike = atm - width
        ce        = round(bs(S, ce_strike, T, r, iv, "call"), 2)
        pe        = round(bs(S, pe_strike, T, r, iv, "put"),  2)
        premium   = round(ce + pe, 2)
        sl        = round(premium * 1.5, 2)
        target    = round(premium * 0.5, 2)
        rr        = round(target / (sl - premium), 2) if sl > premium else 0

        return StrategyResult(
            strategy    = "Short Strangle",
            signal      = "SELL",
            entry       = premium,
            sl          = sl,
            target      = target,
            risk_reward = rr,
            max_profit  = premium,
            max_loss    = None,
            breakeven   = [round(pe_strike - premium, 2), round(ce_strike + premium, 2)],
            legs        = [
                {"action": "SELL", "type": "CE", "strike": ce_strike, "premium": ce},
                {"action": "SELL", "type": "PE", "strike": pe_strike, "premium": pe},
            ],
            description = f"Sell {ce_strike}CE + {pe_strike}PE @ ₹{premium} | SL ₹{sl} | Target ₹{target}",
        )

    @staticmethod
    def iron_condor(S: float, atm: float, T: float, r: float, iv: float, width: float = 200) -> StrategyResult:
        """Iron Condor — sell inner, buy outer strikes."""
        ce_sell = atm + width
        ce_buy  = atm + width * 2
        pe_sell = atm - width
        pe_buy  = atm - width * 2

        ce_s = round(bs(S, ce_sell, T, r, iv, "call"), 2)
        ce_b = round(bs(S, ce_buy,  T, r, iv, "call"), 2)
        pe_s = round(bs(S, pe_sell, T, r, iv, "put"),  2)
        pe_b = round(bs(S, pe_buy,  T, r, iv, "put"),  2)

        premium  = round((ce_s - ce_b) + (pe_s - pe_b), 2)
        max_loss = round(width - premium, 2)
        sl       = round(premium * 0.3, 2)
        target   = round(premium * 0.7, 2)
        rr       = round(target / max_loss, 2) if max_loss else 0

        return StrategyResult(
            strategy    = "Iron Condor",
            signal      = "SELL",
            entry       = premium,
            sl          = sl,
            target      = target,
            risk_reward = rr,
            max_profit  = premium,
            max_loss    = max_loss,
            breakeven   = [round(pe_sell - premium, 2), round(ce_sell + premium, 2)],
            legs        = [
                {"action": "SELL", "type": "CE", "strike": ce_sell, "premium": ce_s},
                {"action": "BUY",  "type": "CE", "strike": ce_buy,  "premium": ce_b},
                {"action": "SELL", "type": "PE", "strike": pe_sell, "premium": pe_s},
                {"action": "BUY",  "type": "PE", "strike": pe_buy,  "premium": pe_b},
            ],
            description = f"Iron Condor {pe_buy}/{pe_sell}/{ce_sell}/{ce_buy} @ ₹{premium} | Max Loss ₹{max_loss}",
        )

    @staticmethod
    def iron_fly(S: float, atm: float, T: float, r: float, iv: float, width: float = 200) -> StrategyResult:
        """Iron Fly — sell ATM straddle, buy wings."""
        ce_sell = atm
        pe_sell = atm
        ce_buy  = atm + width
        pe_buy  = atm - width

        ce_s = round(bs(S, ce_sell, T, r, iv, "call"), 2)
        pe_s = round(bs(S, pe_sell, T, r, iv, "put"),  2)
        ce_b = round(bs(S, ce_buy,  T, r, iv, "call"), 2)
        pe_b = round(bs(S, pe_buy,  T, r, iv, "put"),  2)

        premium  = round((ce_s + pe_s) - (ce_b + pe_b), 2)
        max_loss = round(width - premium, 2)
        sl       = round(premium * 0.3, 2)
        target   = round(premium * 0.7, 2)
        rr       = round(target / max_loss, 2) if max_loss else 0

        return StrategyResult(
            strategy    = "Iron Fly",
            signal      = "SELL",
            entry       = premium,
            sl          = sl,
            target      = target,
            risk_reward = rr,
            max_profit  = premium,
            max_loss    = max_loss,
            breakeven   = [round(atm - premium, 2), round(atm + premium, 2)],
            legs        = [
                {"action": "SELL", "type": "CE", "strike": ce_sell, "premium": ce_s},
                {"action": "SELL", "type": "PE", "strike": pe_sell, "premium": pe_s},
                {"action": "BUY",  "type": "CE", "strike": ce_buy,  "premium": ce_b},
                {"action": "BUY",  "type": "PE", "strike": pe_buy,  "premium": pe_b},
            ],
            description = f"Iron Fly {pe_buy}/{atm}/{ce_buy} @ ₹{premium} | Max Loss ₹{max_loss}",
        )

    @staticmethod
    def bull_call_spread(S: float, K_buy: float, K_sell: float, T: float, r: float, iv: float) -> StrategyResult:
        """Buy lower CE, sell higher CE — mildly bullish."""
        ce_buy  = round(bs(S, K_buy,  T, r, iv, "call"), 2)
        ce_sell = round(bs(S, K_sell, T, r, iv, "call"), 2)
        net     = round(ce_buy - ce_sell, 2)
        max_pft = round((K_sell - K_buy) - net, 2)
        sl      = round(net * 0.5, 2)
        target  = round(max_pft * 0.75, 2)
        rr      = round(target / sl, 2) if sl else 0

        return StrategyResult(
            strategy    = "Bull Call Spread",
            signal      = "BUY",
            entry       = net,
            sl          = sl,
            target      = target,
            risk_reward = rr,
            max_profit  = max_pft,
            max_loss    = net,
            breakeven   = [round(K_buy + net, 2)],
            legs        = [
                {"action": "BUY",  "type": "CE", "strike": K_buy,  "premium": ce_buy},
                {"action": "SELL", "type": "CE", "strike": K_sell, "premium": ce_sell},
            ],
            description = f"Bull Call Spread {K_buy}/{K_sell} @ ₹{net} | Max Profit ₹{max_pft}",
        )

    @staticmethod
    def bear_put_spread(S: float, K_buy: float, K_sell: float, T: float, r: float, iv: float) -> StrategyResult:
        """Buy higher PE, sell lower PE — mildly bearish."""
        pe_buy  = round(bs(S, K_buy,  T, r, iv, "put"), 2)
        pe_sell = round(bs(S, K_sell, T, r, iv, "put"), 2)
        net     = round(pe_buy - pe_sell, 2)
        max_pft = round((K_buy - K_sell) - net, 2)
        sl      = round(net * 0.5, 2)
        target  = round(max_pft * 0.75, 2)
        rr      = round(target / sl, 2) if sl else 0

        return StrategyResult(
            strategy    = "Bear Put Spread",
            signal      = "BUY",
            entry       = net,
            sl          = sl,
            target      = target,
            risk_reward = rr,
            max_profit  = max_pft,
            max_loss    = net,
            breakeven   = [round(K_buy - net, 2)],
            legs        = [
                {"action": "BUY",  "type": "PE", "strike": K_buy,  "premium": pe_buy},
                {"action": "SELL", "type": "PE", "strike": K_sell, "premium": pe_sell},
            ],
            description = f"Bear Put Spread {K_buy}/{K_sell} @ ₹{net} | Max Profit ₹{max_pft}",
        )

    @classmethod
    def all_strategies(cls, S: float, T: float, r: float, iv: float) -> list[dict]:
        """Generate signals for all strategies at current spot."""
        atm   = round(S / 100) * 100
        width = 200
        results = [
            cls.long_call(S, atm, T, r, iv),
            cls.long_put(S, atm, T, r, iv),
            cls.short_straddle(S, atm, T, r, iv),
            cls.short_strangle(S, atm, T, r, iv, width),
            cls.iron_condor(S, atm, T, r, iv, width),
            cls.iron_fly(S, atm, T, r, iv, width),
            cls.bull_call_spread(S, atm, atm + width, T, r, iv),
            cls.bear_put_spread(S, atm, atm - width, T, r, iv),
        ]
        return [r.to_dict() for r in results]
