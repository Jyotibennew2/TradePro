"""Signal Engine — generates BUY / SELL / HOLD signals from OHLCV data."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import pandas as pd


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    symbol: str
    signal: SignalType
    confidence: float          # 0.0 – 1.0
    reason: str
    price: float
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "signal": self.signal.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "price": self.price,
            "timestamp": self.timestamp,
        }


class SignalEngine:
    """
    Rule-based signal engine combining RSI, MACD, EMA crossover,
    and Bollinger Bands.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_period: int = 20,
        bb_std: float = 2.0,
        ema_short: int = 9,
        ema_long: int = 21,
    ):
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.ema_short = ema_short
        self.ema_long = ema_long

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------

    def _rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(self.rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(self.rsi_period).mean()
        rs = gain / loss.replace(0, float("nan"))
        return 100 - (100 / (1 + rs))

    def _macd(self, close: pd.Series):
        ema_fast = close.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.macd_signal, adjust=False).mean()
        return macd_line, signal_line

    def _bollinger(self, close: pd.Series):
        mid = close.rolling(self.bb_period).mean()
        std = close.rolling(self.bb_period).std()
        upper = mid + self.bb_std * std
        lower = mid - self.bb_std * std
        return upper, mid, lower

    # ------------------------------------------------------------------
    # Generate signal
    # ------------------------------------------------------------------

    def generate(self, symbol: str, df: pd.DataFrame) -> Signal:
        """
        df must have columns: open, high, low, close, volume
        Index: DatetimeIndex, sorted ascending.
        """
        if len(df) < self.macd_slow + self.macd_signal:
            raise ValueError("Not enough data to compute indicators.")

        close = df["close"]
        rsi = self._rsi(close)
        macd_line, signal_line = self._macd(close)
        upper_bb, mid_bb, lower_bb = self._bollinger(close)
        ema_s = close.ewm(span=self.ema_short, adjust=False).mean()
        ema_l = close.ewm(span=self.ema_long, adjust=False).mean()

        # Last values
        r = rsi.iloc[-1]
        m = macd_line.iloc[-1]
        sig = signal_line.iloc[-1]
        price = close.iloc[-1]
        es = ema_s.iloc[-1]
        el = ema_l.iloc[-1]
        bb_low = lower_bb.iloc[-1]
        bb_high = upper_bb.iloc[-1]

        bullish_signals = 0
        bearish_signals = 0
        reasons = []

        # RSI
        if r < 35:
            bullish_signals += 1
            reasons.append(f"RSI oversold ({r:.1f})")
        elif r > 65:
            bearish_signals += 1
            reasons.append(f"RSI overbought ({r:.1f})")

        # MACD crossover
        prev_m = macd_line.iloc[-2]
        prev_sig = signal_line.iloc[-2]
        if prev_m < prev_sig and m > sig:
            bullish_signals += 2
            reasons.append("MACD bullish crossover")
        elif prev_m > prev_sig and m < sig:
            bearish_signals += 2
            reasons.append("MACD bearish crossover")

        # EMA crossover
        if es > el:
            bullish_signals += 1
            reasons.append("EMA9 above EMA21")
        else:
            bearish_signals += 1
            reasons.append("EMA9 below EMA21")

        # Bollinger
        if price <= bb_low:
            bullish_signals += 1
            reasons.append("Price at lower Bollinger Band")
        elif price >= bb_high:
            bearish_signals += 1
            reasons.append("Price at upper Bollinger Band")

        total = bullish_signals + bearish_signals
        confidence = max(bullish_signals, bearish_signals) / total if total else 0

        if bullish_signals > bearish_signals:
            signal_type = SignalType.BUY
        elif bearish_signals > bullish_signals:
            signal_type = SignalType.SELL
        else:
            signal_type = SignalType.HOLD
            reasons = ["Indicators mixed"]

        return Signal(
            symbol=symbol,
            signal=signal_type,
            confidence=round(confidence, 2),
            reason="; ".join(reasons),
            price=price,
            timestamp=str(df.index[-1]),
        )
