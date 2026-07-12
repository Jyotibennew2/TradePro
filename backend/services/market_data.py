"""
TradePro Backend - Market Data Service
Quotes, LTP, OHLC, Option Chain, Historical candles, Live cache.
Compatible with Python 3.11+, Termux, Linux.
"""

import time
import math
import random
import logging
from typing import Optional
from backend.cache import quote_cache, chain_cache
from backend.fyers_service import FyersService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Market Data Service
# ---------------------------------------------------------------------------

class MarketDataService:
    """
    Centralized market data service with caching.
    All data flows through here — never call FyersService directly from routes.
    """

    def __init__(self, svc: FyersService) -> None:
        self._svc = svc

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------

    def get_quotes(self, symbols: str = "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX") -> dict:
        """Return quotes with cache (3s TTL)."""
        cached = quote_cache.get(symbols)
        if cached:
            return cached
        result = self._svc.get_quotes(symbols)
        if result.get("success"):
            quote_cache.set(symbols, result)
        return result

    def get_ltp(self, symbol: str) -> float:
        """Return LTP for a single symbol."""
        quotes = self.get_quotes(symbol)
        data   = quotes.get("data", {})
        return data.get(symbol, {}).get("ltp", 0.0)

    def get_ohlc(self, symbol: str) -> dict:
        """Return OHLC for a single symbol."""
        quotes = self.get_quotes(symbol)
        data   = quotes.get("data", {})
        item   = data.get(symbol, {})
        return {
            "symbol": symbol,
            "ltp"   : item.get("ltp",   0.0),
            "open"  : item.get("open",  0.0),
            "high"  : item.get("high",  0.0),
            "low"   : item.get("low",   0.0),
            "close" : item.get("close", 0.0),
            "ch"    : item.get("ch",    0.0),
            "chp"   : item.get("chp",   0.0),
        }

    # ------------------------------------------------------------------
    # Option Chain
    # ------------------------------------------------------------------

    def get_option_chain(
        self,
        symbol      : str = "NIFTY",
        expiry      : str = "",
        strike_count: int = 10,
    ) -> dict:
        """Return option chain with cache (10s TTL)."""
        cache_key = f"chain:{symbol}:{expiry}:{strike_count}"
        cached    = chain_cache.get(cache_key)
        if cached:
            return cached
        result = self._svc.get_option_chain(symbol=symbol, expiry=expiry, strike_count=strike_count)
        if result.get("success"):
            chain_cache.set(cache_key, result)
        return result

    # ------------------------------------------------------------------
    # Historical candles
    # ------------------------------------------------------------------

    def get_historical(
        self,
        symbol    : str,
        days      : int   = 30,
        interval  : str   = "1D",
    ) -> dict:
        """
        Return historical OHLCV candles.
        Delegates to FyersService.get_history() — returns real Fyers data
        when authenticated, realistic mock data otherwise.
        """
        cache_key = f"hist:{symbol}:{days}:{interval}"
        cached    = chain_cache.get(cache_key)
        if cached:
            return cached

        resolution = "D" if interval in ("1D", "D") else interval
        hist       = self._svc.get_history(symbol, days=days, resolution=resolution)

        result = {
            "success" : hist.get("success", True),
            "symbol"  : symbol,
            "interval": interval,
            "candles" : hist.get("candles", []),
            "mock"    : hist.get("mock", True),
        }
        chain_cache.set(cache_key, result, ttl=300)
        return result

    # ------------------------------------------------------------------
    # Auto refresh (called by scheduler)
    # ------------------------------------------------------------------

    def refresh_quotes(self) -> None:
        """Force refresh quotes cache."""
        quote_cache.delete("NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,NSE:NIFTYMID100-INDEX")
        self.get_quotes()
        logger.debug("Quotes cache refreshed")

    def refresh_chain(self, symbol: str = "NIFTY") -> None:
        """Force refresh option chain cache."""
        chain_cache.delete(f"chain:{symbol}::")
        self.get_option_chain(symbol)
        logger.debug(f"Option chain cache refreshed: {symbol}")
