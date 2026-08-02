"""
TradePro Backend - Delta Exchange MarketDataProvider Adapter

Wraps the existing, already-verified backend.services.delta_service
functions (get_expiries, get_option_chain - both already powering the
5-min archiver and /api/delta/optionchain* routes) behind the
MarketDataProvider interface, so code that depends on the interface
(rather than a concrete module) can use Delta Exchange the same way it
uses FyersService.

Deliberately does NOT touch backend.services.delta_service itself - that
module is live in production (24/7 archiving scheduler task), so this
adapter is purely additive: a thin wrapper, zero changes to proven code.

get_quotes(): reuses get_option_chain()'s spot price (that field is
already fetched and verified working in production) rather than guessing
at an undocumented separate spot/ticker endpoint.

get_history(): NOT wired to any route today, so rather than guess at an
untested Delta candles endpoint and risk silently-wrong data going
unnoticed, this raises NotImplementedError with a clear message. Implement
properly (and test against a real route) before any caller depends on it.
"""

from backend.brokers.base import MarketDataProvider
from backend.services import delta_service


class DeltaMarketDataProvider(MarketDataProvider):
    """MarketDataProvider adapter for Delta Exchange India (BTC/ETH options - crypto, 24/7)."""

    def get_quotes(self, symbols: str) -> dict:
        """
        `symbols` is a comma-separated list of underlyings, e.g. "BTC,ETH"
        (Delta's own symbol format, not Fyers' "NSE:..." style - callers
        that need to normalize across brokers should do so at the call site).
        """
        result: dict = {}
        for underlying in [s.strip().upper() for s in symbols.split(",") if s.strip()]:
            if underlying not in delta_service.SUPPORTED_UNDERLYINGS:
                continue
            expiries = delta_service.get_expiries(underlying, max_expiries=1)
            if not expiries:
                continue
            chain = delta_service.get_option_chain(underlying, expiries[0])
            if chain.get("success"):
                result[underlying] = {"ltp": chain.get("spot", 0)}
        return {"success": bool(result), "mock": False, "data": result}

    def get_history(self, symbol: str, days: int, resolution: str) -> dict:
        raise NotImplementedError(
            "DeltaMarketDataProvider.get_history is not implemented yet - "
            "no verified Delta Exchange candles endpoint is wired up. "
            "Implement and test against a real route before use."
        )

    def get_expiries(self, symbol: str) -> dict:
        expiries = delta_service.get_expiries(symbol.upper())
        return {"success": True, "mock": False, "expiries": expiries}

    def get_option_chain(self, symbol: str, expiry: str, strike_count: int = 0) -> dict:
        # strike_count is unused for Delta - it always returns the full
        # chain for the expiry (Delta doesn't support a strike-count param
        # the way Fyers does); kept in the signature to satisfy the shared
        # interface without breaking callers that pass it positionally.
        return delta_service.get_option_chain(symbol.upper(), expiry)
