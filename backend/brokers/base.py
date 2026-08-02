"""
TradePro Backend - Broker Adapter Interface

Defines a common contract that any broker/data-provider integration must
satisfy, so the rest of TradePro (server.py routes, MarketDataService,
scheduler tasks) can work with "a broker" without caring which one is
actually wired up underneath.

DESIGN NOTE (license compliance): This interface was designed from scratch
by studying general broker-integration patterns (the idea of a common
adapter interface with a mock-data fallback is a widely-used, unpatentable
architectural pattern - not copyrighted expression). No code was copied
from any third-party project. In particular, OpenAlgo (github.com/marketcalls/openalgo)
is AGPL-3.0 licensed, which would require this entire codebase to become
open-source if any of its actual code were reused in a project run as a
network service (which TradePro is) - so nothing from it was copied, only
the general "pluggable broker adapter" concept was used as inspiration.

Two-tier interface:
  MarketDataProvider - read-only market data (quotes, history, option chain).
                        Delta Exchange (crypto) implements only this tier -
                        it's used for market data, not order execution.
  BrokerAdapter       - extends MarketDataProvider with order/account
                         operations (funds, orders, positions, place_order).
                         Fyers implements this full tier.

Every method returns the same envelope shape TradePro already uses
throughout the codebase: {"success": bool, "mock": bool, ...data-specific...}
so adopting this interface doesn't change any existing response format.
"""

from abc import ABC, abstractmethod


class MarketDataProvider(ABC):
    """Read-only market data contract - quotes, historical candles, option chain."""

    @abstractmethod
    def get_quotes(self, symbols: str) -> dict:
        """Live (or mock) quotes for one or more symbols."""
        raise NotImplementedError

    @abstractmethod
    def get_history(self, symbol: str, days: int, resolution: str) -> dict:
        """Live (or mock) historical candles."""
        raise NotImplementedError

    @abstractmethod
    def get_expiries(self, symbol: str) -> dict:
        """Available option-contract expiry dates for a symbol."""
        raise NotImplementedError

    @abstractmethod
    def get_option_chain(self, symbol: str, expiry: str, strike_count: int) -> dict:
        """Live (or mock) option chain for a symbol/expiry."""
        raise NotImplementedError


class BrokerAdapter(MarketDataProvider):
    """
    Full broker contract: market data (inherited) plus account/order
    operations. Implement this for any broker that can actually place
    trades (as opposed to a data-only source like Delta Exchange).
    """

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Whether this broker session currently has a valid, working token."""
        raise NotImplementedError

    @abstractmethod
    def get_funds(self) -> dict:
        """Live (or mock) account funds/margin."""
        raise NotImplementedError

    @abstractmethod
    def get_orders(self) -> dict:
        """Live (or mock) order book."""
        raise NotImplementedError

    @abstractmethod
    def place_order(self, order: dict) -> dict:
        """Place a live (or mock) order."""
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> dict:
        """Live (or mock) open positions."""
        raise NotImplementedError
