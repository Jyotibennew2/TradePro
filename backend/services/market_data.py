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
        every CE/PE entry — backed out from the real traded LTP. Handles
        both response shapes:
          - mock/reconstructed: data.expiryData = [{strike, ce_ltp, pe_ltp, ...}]
          - live Fyers         : data.optionsChain = [{strike_price, option_type, ltp, ...}]
        """
        try:
            data = chain_result.get("data", {})
            T    = max(days_to_expiry, 0.5) / 365

            # Mock / reconstructed shape — already strike-keyed rows
            expiry_data = data.get("expiryData")
            if expiry_data and isinstance(expiry_data, list) and expiry_data and "strike" in expiry_data[0]:
                spot = chain_result.get("spot", 0) or 0
                if not spot:
                    return
                for row in expiry_data:
                    strike = row.get("strike")
                    if strike is None:
                        continue
                    if row.get("ce_ltp"):
                        g = GreeksEngine.calculate(spot, strike, T, RISK_FREE_RATE, 0.15, "call", market_price=row["ce_ltp"])
                        row["ce_iv"], row["ce_delta"], row["ce_gamma"], row["ce_theta"], row["ce_vega"] = g.iv, g.delta, g.gamma, g.theta, g.vega
                    if row.get("pe_ltp"):
                        g = GreeksEngine.calculate(spot, strike, T, RISK_FREE_RATE, 0.15, "put", market_price=row["pe_ltp"])
                        row["pe_iv"], row["pe_delta"], row["pe_gamma"], row["pe_theta"], row["pe_vega"] = g.iv, g.delta, g.gamma, g.theta, g.vega
                return

            # Live Fyers shape — flat list, one row per contract, spot carried on the "" option_type row
            options_chain = data.get("optionsChain")
            if options_chain and isinstance(options_chain, list):
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
        Delegates to FyersService.get_history() — returns real Fyers data
        when authenticated, realistic mock data otherwise.
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
