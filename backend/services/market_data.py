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
from backend.validators import RESOLUTION_MAP, clamp_days_for_resolution
from backend.greeks import GreeksEngine

logger = logging.getLogger(__name__)

RISK_FREE_RATE          = 0.065
DEFAULT_DAYS_TO_EXPIRY  = 7   # weekly index options — used only when the feed doesn't tell us the real expiry


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
    # Expiries
    # ------------------------------------------------------------------

    def get_expiries(self, symbol: str) -> dict:
        """Return the list of available option expiries (weekly + monthly), cached 5 min."""
        cache_key = f"expiries:{symbol}"
        cached    = chain_cache.get(cache_key)
        if cached:
            return cached
        result = self._svc.get_expiries(symbol)
        if result.get("success"):
            chain_cache.set(cache_key, result, ttl=300)
        return result

    # ------------------------------------------------------------------
    # Option Chain
    # ------------------------------------------------------------------

    def get_option_chain(
        self,
        symbol      : str = "NIFTY",
        expiry      : str = "",
        strike_count: int = 10,
        days_to_expiry: Optional[float] = None,
    ) -> dict:
        """Return option chain (with IV + Greeks enriched) with cache (10s TTL)."""
        cache_key = f"chain:{symbol}:{expiry}:{strike_count}"
        cached    = chain_cache.get(cache_key)
        if cached:
            return cached
        result = self._svc.get_option_chain(symbol=symbol, expiry=expiry, strike_count=strike_count)
        if result.get("success"):
            self._enrich_with_greeks(result, days_to_expiry or DEFAULT_DAYS_TO_EXPIRY)
            chain_cache.set(cache_key, result)
        return result

    def _enrich_with_greeks(self, chain_result: dict, days_to_expiry: float) -> None:
        """
        Mutates chain_result in place, adding iv/delta/gamma/theta/vega to
        every CE/PE entry — backed out from the real traded LTP.

        Only handles the live Fyers response shape:
          data.optionsChain = [{strike_price, option_type, ltp, ...}]
        This is the only shape FyersService.get_option_chain() ever returns
        on success — there is no mock/reconstructed fallback anywhere in
        this service, so no other shape needs to be handled here.
        """
        try:
            data = chain_result.get("data", {})
            T    = max(days_to_expiry, 0.5) / 365

            options_chain = data.get("optionsChain")
            if not options_chain or not isinstance(options_chain, list):
                return

            spot = 0.0
            for item in options_chain:
                if item.get("option_type", "") == "":
                    spot = item.get("ltp", 0) or spot
                    break
            if not spot:
                return
            otype_map = {"CE": "call", "PE": "put"}
            for item in options_chain:
                otype = otype_map.get(item.get("option_type"))
                strike = item.get("strike_price")
                ltp    = item.get("ltp")
                if not otype or strike is None or not ltp:
                    continue
                g = GreeksEngine.calculate(spot, strike, T, RISK_FREE_RATE, 0.15, otype, market_price=ltp)
                item["iv"], item["delta"], item["gamma"], item["theta"], item["vega"] = g.iv, g.delta, g.gamma, g.theta, g.vega
        except Exception as e:
            logger.warning(f"Greeks enrichment failed: {e}")

    # ------------------------------------------------------------------
    # Historical candles
    # ------------------------------------------------------------------

    def get_historical(
        self,
        symbol    : str,
        days      : int = 30,
        interval  : str = "1d",
    ) -> dict:
        """
        Return historical OHLCV candles for a given timeframe.

        interval accepts friendly names: "5m", "15m", "30m", "1h", "2h", "1d"
        (legacy "1D"/"D" also accepted and treated as "1d").
        Delegates to FyersService.get_history() — real Fyers data on
        success, or an explicit error/empty result when unavailable. There
        is no mock fallback; the "mock" key on the returned dict reflects
        exactly what get_history() reported.
        """
        norm = interval.lower() if interval not in ("D", "1D") else "1d"
        resolution = RESOLUTION_MAP.get(norm, "D")
        days       = clamp_days_for_resolution(days, norm)

        cache_key = f"hist:{symbol}:{days}:{norm}"
        cached    = chain_cache.get(cache_key)
        if cached:
            return cached

        hist = self._svc.get_history(symbol, days=days, resolution=resolution)

        result = {
            "success"   : hist.get("success", True),
            "symbol"    : symbol,
            "interval"  : norm,
            "days_used" : days,
            "candles"   : hist.get("candles", []),
            "mock"      : hist.get("mock", True),
        }
        chain_cache.set(cache_key, result, ttl=300)
        return result

    # ------------------------------------------------------------------
    # Auto refresh (called by scheduler)
    # ------------------------------------------------------------------

    def refresh_quotes(self) -> bool:
        """
        Force refresh quotes cache. Returns True/False based on whether the
        underlying Fyers call actually succeeded — the scheduler uses this
        to back off automatically after repeated failures (e.g. a rate
        limit) instead of retrying at full frequency forever.
        """
        quote_cache.delete("NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,NSE:NIFTYMID100-INDEX")
        result = self.get_quotes()
        ok = bool(result.get("success"))
        if ok:
            logger.debug("Quotes cache refreshed")
        return ok

    def refresh_chain(self, symbol: str = "NIFTY") -> bool:
        """
        Force refresh option chain cache. Returns True/False based on
        whether the underlying Fyers call actually succeeded — same
        backoff purpose as refresh_quotes().
        """
        chain_cache.delete(f"chain:{symbol}::")
        result = self.get_option_chain(symbol)
        ok = bool(result.get("success"))
        if ok:
            logger.debug(f"Option chain cache refreshed: {symbol}")
        return ok
