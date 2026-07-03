"""
TradePro Backend - Pricing Engine
Black-Scholes options pricing with Greeks.
Compatible with Python 3.11+, Termux, Linux.
"""

import math
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normal distribution helpers (no scipy dependency)
# ---------------------------------------------------------------------------

def _erf(x: float) -> float:
    """Abramowitz & Stegun approximation — max error 1.5e-7."""
    sign = 1 if x >= 0 else -1
    x    = abs(x)
    t    = 1.0 / (1.0 + 0.3275911 * x)
    y    = 1.0 - (((((1.061405429 * t - 1.453152027) * t)
                    + 1.421413741) * t - 0.284496736) * t
                  + 0.254829592) * t * math.exp(-x * x)
    return sign * y


def _norm_cdf(x: float) -> float:
    """Cumulative standard normal distribution N(x)."""
    return 0.5 * (1.0 + _erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class BSResult:
    price : float
    delta : float
    gamma : float
    theta : float   # per calendar day
    vega  : float   # per 1% move in vol


# ---------------------------------------------------------------------------
# Black-Scholes class
# ---------------------------------------------------------------------------

class BlackScholes:
    """
    Black-Scholes European options pricing engine with full Greeks.

    Parameters
    ----------
    S          : Spot price
    K          : Strike price
    T          : Time to expiry in years (e.g. 30/365)
    r          : Risk-free rate           (e.g. 0.065)
    sigma      : Implied volatility       (e.g. 0.18)
    option_type: 'call' or 'put'
    """

    def __init__(
        self,
        S           : float,
        K           : float,
        T           : float,
        r           : float,
        sigma       : float,
        option_type : str = "call",
    ) -> None:
        if S <= 0:
            raise ValueError(f"Spot must be positive, got {S}")
        if K <= 0:
            raise ValueError(f"Strike must be positive, got {K}")
        if sigma <= 0:
            raise ValueError(f"Volatility must be positive, got {sigma}")

        self.S           = S
        self.K           = K
        self.T           = max(T, 1e-9)
        self.r           = r
        self.sigma       = sigma
        self.option_type = option_type.lower()
        self._expired    = T <= 0

        if self.option_type not in ("call", "put"):
            raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")

        self._d1: float = 0.0
        self._d2: float = 0.0
        if not self._expired:
            self._compute_d()

    def _compute_d(self) -> None:
        sqrt_T   = math.sqrt(self.T)
        self._d1 = (math.log(self.S / self.K) + (self.r + 0.5 * self.sigma ** 2) * self.T) / (self.sigma * sqrt_T)
        self._d2 = self._d1 - self.sigma * sqrt_T

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def price(self) -> float:
        """Theoretical option price."""
        if self._expired:
            return max(self.S - self.K, 0.0) if self.option_type == "call" else max(self.K - self.S, 0.0)
        disc = math.exp(-self.r * self.T)
        if self.option_type == "call":
            return self.S * _norm_cdf(self._d1) - self.K * disc * _norm_cdf(self._d2)
        return self.K * disc * _norm_cdf(-self._d2) - self.S * _norm_cdf(-self._d1)

    def delta(self) -> float:
        """Delta — sensitivity of price to spot move."""
        if self._expired:
            if self.option_type == "call":
                return 1.0 if self.S > self.K else 0.0
            return -1.0 if self.S < self.K else 0.0
        if self.option_type == "call":
            return _norm_cdf(self._d1)
        return _norm_cdf(self._d1) - 1.0

    def gamma(self) -> float:
        """Gamma — rate of change of delta per unit spot move."""
        if self._expired:
            return 0.0
        return _norm_pdf(self._d1) / (self.S * self.sigma * math.sqrt(self.T))

    def theta(self) -> float:
        """Theta — time decay per calendar day (negative for long options)."""
        if self._expired:
            return 0.0
        disc  = math.exp(-self.r * self.T)
        term1 = -(self.S * _norm_pdf(self._d1) * self.sigma) / (2.0 * math.sqrt(self.T))
        term2 = (-self.r * self.K * disc * _norm_cdf(self._d2)
                 if self.option_type == "call"
                 else self.r * self.K * disc * _norm_cdf(-self._d2))
        return (term1 + term2) / 365.0

    def vega(self) -> float:
        """Vega — price sensitivity per 1% change in implied volatility."""
        if self._expired:
            return 0.0
        return self.S * _norm_pdf(self._d1) * math.sqrt(self.T) / 100.0

    def all_greeks(self) -> BSResult:
        """Return price + all Greeks in one call."""
        return BSResult(
            price = self.price(),
            delta = self.delta(),
            gamma = self.gamma(),
            theta = self.theta(),
            vega  = self.vega(),
        )


# ---------------------------------------------------------------------------
# Convenience function — drop-in replacement for old bs() in server.py
# ---------------------------------------------------------------------------

def bs(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
    """Returns theoretical option price. Safe — never raises."""
    try:
        return BlackScholes(S, K, T, r, sigma, option_type).price()
    except Exception as e:
        logger.warning(f"bs() error: {e}")
        return 0.0
