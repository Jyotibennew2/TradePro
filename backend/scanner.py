"""
TradePro Backend - Scanner Engine
EMA, VWAP, RSI, Breakout, Volume, OHL, Gap, Inside Candle scanners.
Compatible with Python 3.11+, Termux, Linux.
"""

import math
import logging
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    scanner   : str
    symbol    : str
    signal    : str      # BUY / SELL / NEUTRAL
    value     : float
    condition : str
    strength  : str      # STRONG / MODERATE / WEAK

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _ema(prices: list[float], period: int) -> list[float]:
    """Calculate EMA for a list of prices."""
    if len(prices) < period:
        return []
    k      = 2 / (period + 1)
    result = [sum(prices[:period]) / period]
    for p in prices[period:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def _rsi(prices: list[float], period: int = 14) -> float:
    """Calculate RSI."""
    if len(prices) < period + 1:
        return 50.0
    gains  = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _vwap(prices: list[float], volumes: list[float]) -> float:
    """Calculate VWAP."""
    if not prices or not volumes or len(prices) != len(volumes):
        return 0.0
    total_vol = sum(volumes)
    if total_vol == 0:
        return 0.0
    return round(sum(p * v for p, v in zip(prices, volumes)) / total_vol, 2)


# ---------------------------------------------------------------------------
# Scanner Engine
# ---------------------------------------------------------------------------

class ScannerEngine:
    """
    Run technical scanners on price data.
    All scanners return ScanResult with signal and strength.
    """

    # ------------------------------------------------------------------
    # EMA Scanner
    # ------------------------------------------------------------------

    @staticmethod
    def ema_scanner(
        symbol : str,
        prices : list[float],
        fast   : int = 9,
        slow   : int = 21,
    ) -> ScanResult:
        """EMA crossover scanner."""
        ema_fast = _ema(prices, fast)
        ema_slow = _ema(prices, slow)

        if not ema_fast or not ema_slow:
            return ScanResult(scanner="EMA", symbol=symbol, signal="NEUTRAL",
                              value=0.0, condition="Insufficient data", strength="WEAK")

        f, s   = ema_fast[-1], ema_slow[-1]
        diff   = round(((f - s) / s) * 100, 3)
        signal = "BUY" if f > s else "SELL"
        strength = "STRONG" if abs(diff) > 0.5 else "MODERATE" if abs(diff) > 0.2 else "WEAK"

        return ScanResult(
            scanner   = "EMA",
            symbol    = symbol,
            signal    = signal,
            value     = round(f, 2),
            condition = f"EMA{fast}={round(f,2)} {'>' if f>s else '<'} EMA{slow}={round(s,2)}",
            strength  = strength,
        )

    # ------------------------------------------------------------------
    # RSI Scanner
    # ------------------------------------------------------------------

    @staticmethod
    def rsi_scanner(
        symbol    : str,
        prices    : list[float],
        period    : int   = 14,
        oversold  : float = 30.0,
        overbought: float = 70.0,
    ) -> ScanResult:
        """RSI overbought/oversold scanner."""
        rsi    = _rsi(prices, period)
        signal = "BUY" if rsi < oversold else "SELL" if rsi > overbought else "NEUTRAL"
        strength = (
            "STRONG"   if (rsi < 25 or rsi > 75)  else
            "MODERATE" if (rsi < 35 or rsi > 65)  else
            "WEAK"
        )
        condition = (
            f"RSI={rsi} OVERSOLD (<{oversold})"  if rsi < oversold  else
            f"RSI={rsi} OVERBOUGHT (>{overbought})" if rsi > overbought else
            f"RSI={rsi} NEUTRAL"
        )
        return ScanResult(scanner="RSI", symbol=symbol, signal=signal,
                          value=rsi, condition=condition, strength=strength)

    # ------------------------------------------------------------------
    # VWAP Scanner
    # ------------------------------------------------------------------

    @staticmethod
    def vwap_scanner(
        symbol : str,
        prices : list[float],
        volumes: list[float],
    ) -> ScanResult:
        """Price vs VWAP scanner."""
        vwap   = _vwap(prices, volumes)
        ltp    = prices[-1] if prices else 0.0
        diff   = round(((ltp - vwap) / vwap) * 100, 3) if vwap else 0.0
        signal = "BUY" if ltp > vwap else "SELL"
        strength = "STRONG" if abs(diff) > 0.5 else "MODERATE" if abs(diff) > 0.2 else "WEAK"

        return ScanResult(
            scanner   = "VWAP",
            symbol    = symbol,
            signal    = signal,
            value     = vwap,
            condition = f"LTP={ltp} {'>' if ltp>vwap else '<'} VWAP={vwap} ({diff:+.2f}%)",
            strength  = strength,
        )

    # ------------------------------------------------------------------
    # Breakout Scanner
    # ------------------------------------------------------------------

    @staticmethod
    def breakout_scanner(
        symbol    : str,
        prices    : list[float],
        period    : int = 20,
    ) -> ScanResult:
        """Price breakout above/below N-period high/low."""
        if len(prices) < period + 1:
            return ScanResult(scanner="Breakout", symbol=symbol, signal="NEUTRAL",
                              value=0.0, condition="Insufficient data", strength="WEAK")

        window   = prices[-period - 1:-1]
        high     = max(window)
        low      = min(window)
        ltp      = prices[-1]
        signal   = "BUY" if ltp > high else "SELL" if ltp < low else "NEUTRAL"
        strength = "STRONG" if signal != "NEUTRAL" else "WEAK"

        return ScanResult(
            scanner   = "Breakout",
            symbol    = symbol,
            signal    = signal,
            value     = ltp,
            condition = f"LTP={ltp} | {period}D High={high} Low={low}",
            strength  = strength,
        )

    # ------------------------------------------------------------------
    # Volume Breakout
    # ------------------------------------------------------------------

    @staticmethod
    def volume_breakout(
        symbol     : str,
        volumes    : list[float],
        multiplier : float = 2.0,
    ) -> ScanResult:
        """Volume spike scanner."""
        if len(volumes) < 10:
            return ScanResult(scanner="VolumeBreakout", symbol=symbol, signal="NEUTRAL",
                              value=0.0, condition="Insufficient data", strength="WEAK")

        avg_vol  = sum(volumes[-10:-1]) / 9
        cur_vol  = volumes[-1]
        ratio    = round(cur_vol / avg_vol, 2) if avg_vol else 0.0
        signal   = "BUY" if ratio >= multiplier else "NEUTRAL"
        strength = "STRONG" if ratio >= 3.0 else "MODERATE" if ratio >= 2.0 else "WEAK"

        return ScanResult(
            scanner   = "VolumeBreakout",
            symbol    = symbol,
            signal    = signal,
            value     = cur_vol,
            condition = f"Volume={cur_vol:.0f} Avg={avg_vol:.0f} Ratio={ratio}x",
            strength  = strength,
        )

    # ------------------------------------------------------------------
    # Open High Low (OHL)
    # ------------------------------------------------------------------

    @staticmethod
    def ohl_scanner(
        symbol: str,
        open_ : float,
        high  : float,
        low   : float,
        ltp   : float,
    ) -> ScanResult:
        """Open = High or Open = Low scanner."""
        if abs(open_ - low) < 0.5:
            signal    = "BUY"
            condition = f"Open≈Low: O={open_} L={low} → Bullish"
            strength  = "STRONG"
        elif abs(open_ - high) < 0.5:
            signal    = "SELL"
            condition = f"Open≈High: O={open_} H={high} → Bearish"
            strength  = "STRONG"
        else:
            signal    = "NEUTRAL"
            condition = f"O={open_} H={high} L={low} — No OHL pattern"
            strength  = "WEAK"

        return ScanResult(scanner="OHL", symbol=symbol, signal=signal,
                          value=ltp, condition=condition, strength=strength)

    # ------------------------------------------------------------------
    # Gap Up / Gap Down
    # ------------------------------------------------------------------

    @staticmethod
    def gap_scanner(
        symbol     : str,
        prev_close : float,
        open_      : float,
        gap_pct    : float = 0.5,
    ) -> ScanResult:
        """Gap up/down scanner."""
        gap    = round(((open_ - prev_close) / prev_close) * 100, 2) if prev_close else 0.0
        signal = "BUY" if gap >= gap_pct else "SELL" if gap <= -gap_pct else "NEUTRAL"
        strength = "STRONG" if abs(gap) >= 1.0 else "MODERATE" if abs(gap) >= 0.5 else "WEAK"

        return ScanResult(
            scanner   = "Gap",
            symbol    = symbol,
            signal    = signal,
            value     = gap,
            condition = f"Gap={gap:+.2f}% | Prev Close={prev_close} Open={open_}",
            strength  = strength,
        )

    # ------------------------------------------------------------------
    # Inside Candle
    # ------------------------------------------------------------------

    @staticmethod
    def inside_candle(
        symbol      : str,
        prev_high   : float,
        prev_low    : float,
        curr_high   : float,
        curr_low    : float,
    ) -> ScanResult:
        """Inside candle (consolidation) scanner."""
        is_inside = curr_high <= prev_high and curr_low >= prev_low
        signal    = "NEUTRAL" if is_inside else "NEUTRAL"
        condition = (
            f"Inside Candle: PH={prev_high} PL={prev_low} CH={curr_high} CL={curr_low}"
            if is_inside else
            f"Not Inside: PH={prev_high} PL={prev_low} CH={curr_high} CL={curr_low}"
        )
        return ScanResult(
            scanner   = "InsideCandle",
            symbol    = symbol,
            signal    = "NEUTRAL",
            value     = curr_high - curr_low,
            condition = condition,
            strength  = "MODERATE" if is_inside else "WEAK",
        )

    # ------------------------------------------------------------------
    # Run all scanners
    # ------------------------------------------------------------------

    @classmethod
    def run_all(
        cls,
        symbol    : str,
        prices    : list[float],
        volumes   : list[float],
        open_     : float = 0.0,
        high      : float = 0.0,
        low       : float = 0.0,
        prev_close: float = 0.0,
    ) -> list[dict]:
        """Run all scanners and return results."""
        results = []
        ltp = prices[-1] if prices else 0.0

        results.append(cls.ema_scanner(symbol, prices).to_dict())
        results.append(cls.rsi_scanner(symbol, prices).to_dict())
        results.append(cls.vwap_scanner(symbol, prices, volumes).to_dict())
        results.append(cls.breakout_scanner(symbol, prices).to_dict())
        results.append(cls.volume_breakout(symbol, volumes).to_dict())

        if open_ and high and low:
            results.append(cls.ohl_scanner(symbol, open_, high, low, ltp).to_dict())

        if prev_close and open_:
            results.append(cls.gap_scanner(symbol, prev_close, open_).to_dict())

        return results
