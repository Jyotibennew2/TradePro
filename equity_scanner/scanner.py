"""Equity Scanner — filters a universe of stocks by technical criteria."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

import pandas as pd

from signal_engine import Signal, SignalEngine, SignalType


@dataclass
class ScanFilter:
    min_rsi: Optional[float] = None
    max_rsi: Optional[float] = None
    signal: Optional[SignalType] = None
    min_confidence: float = 0.0
    min_volume: Optional[float] = None   # average daily volume
    min_price: Optional[float] = None
    max_price: Optional[float] = None


@dataclass
class ScanResult:
    symbol: str
    signal: Signal
    avg_volume: float
    last_price: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "signal": self.signal.to_dict(),
            "avg_volume": self.avg_volume,
            "last_price": self.last_price,
        }


class EquityScanner:
    """
    Scans a universe of symbols concurrently and returns those
    matching the supplied ScanFilter.
    """

    def __init__(
        self,
        data_fetcher: Callable[[str], Awaitable[pd.DataFrame]],
        engine: Optional[SignalEngine] = None,
        concurrency: int = 10,
    ):
        """
        data_fetcher: async function(symbol) -> pd.DataFrame with OHLCV columns
        engine: optional custom SignalEngine instance
        """
        self._fetch = data_fetcher
        self._engine = engine or SignalEngine()
        self._concurrency = concurrency

    async def scan(
        self,
        universe: List[str],
        scan_filter: ScanFilter,
    ) -> List[ScanResult]:
        sem = asyncio.Semaphore(self._concurrency)
        tasks = [self._scan_one(sym, scan_filter, sem) for sym in universe]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, ScanResult)]

    async def _scan_one(
        self,
        symbol: str,
        f: ScanFilter,
        sem: asyncio.Semaphore,
    ) -> Optional[ScanResult]:
        async with sem:
            try:
                df = await self._fetch(symbol)
                if df is None or df.empty:
                    return None

                signal = self._engine.generate(symbol, df)

                avg_vol = df["volume"].tail(20).mean()
                last_price = df["close"].iloc[-1]

                # Apply filters
                if f.signal and signal.signal != f.signal:
                    return None
                if signal.confidence < f.min_confidence:
                    return None
                if f.min_volume and avg_vol < f.min_volume:
                    return None
                if f.min_price and last_price < f.min_price:
                    return None
                if f.max_price and last_price > f.max_price:
                    return None

                return ScanResult(
                    symbol=symbol,
                    signal=signal,
                    avg_volume=round(avg_vol, 0),
                    last_price=last_price,
                )
            except Exception as exc:
                # Log in production; silently skip here
                return None
