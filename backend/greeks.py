"""
TradePro Backend - Greeks Engine
Calculate Delta, Gamma, Theta, Vega, Rho, IV.
Uses Black-Scholes from pricing.py.
Compatible with Python 3.11+, Termux, Linux.
"""

import math
import logging
from dataclasses import dataclass, asdict
from typing import Optional

from backend.pricing import BlackScholes, _norm_cdf, _norm_pdf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class Greeks:
    delta : float
    gamma : float
    theta : float
    vega  : float
    rho   : float
    iv    : float
    price : float

    def to_dict(self) -> dict:
        return {k: round(v, 6) for k, v in asdict(self).items()}


# ---------------------------------------------------------------------------
# IV solver (bisection method)
# ---------------------------------------------------------------------------

def calculate_iv(
    market_price : float,
    S            : float,
    K            : float,
    T            : float,
    r            : float,
    option_type  : str,
    precision    : float = 0.0001,
    max_iter     : int   = 200,
) -> float:
    """
    Calculate Implied Volatility using bisection method.
    Returns IV as decimal (e.g. 0.18 for 18%).
    Returns 0.0 if not solvable.
    """
    if T <= 0 or market_price <= 0:
        return 0.0

    lo, hi = 0.001, 5.0

    for _ in range(max_iter):
        mid   = (lo + hi) / 2.0
        price = BlackScholes(S, K, T, r, mid, option_type).price()

        if abs(price - market_price) < precision:
            return round(mid, 6)
        if price < market_price:
            lo = mid
        else:
            hi = mid

    logger.debug(f"IV solver did not converge for S={S} K={K} T={T} price={market_price}")
    return round((lo + hi) / 2.0, 6)


# ---------------------------------------------------------------------------
# Rho
# ---------------------------------------------------------------------------

def _rho(
    S          : float,
    K          : float,
    T          : float,
    r          : float,
    sigma      : float,
    option_type: str,
) -> float:
    """Rho — sensitivity to interest rate (per 1% change)."""
    if T <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1     = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2     = d1 - sigma * sqrt_T
    disc   = math.exp(-r * T)
    if option_type.lower() == "call":
        return K * T * disc * _norm_cdf(d2) / 100.0
    return -K * T * disc * _norm_cdf(-d2) / 100.0


# ---------------------------------------------------------------------------
# Main Greeks calculator
# ---------------------------------------------------------------------------

class GreeksEngine:
    """Calculate all option Greeks including IV."""

    @staticmethod
    def calculate(
        S           : float,
        K           : float,
        T           : float,
        r           : float,
        sigma       : float,
        option_type : str,
        market_price: Optional[float] = None,
    ) -> Greeks:
        """
        Calculate all Greeks.

        Parameters
        ----------
        S            : Spot price
        K            : Strike price
        T            : Time to expiry in years
        r            : Risk-free rate
        sigma        : Volatility (used if market_price not provided)
        option_type  : 'call' or 'put'
        market_price : If provided, IV is calculated from this price
        """
        try:
            bs  = BlackScholes(S, K, T, r, sigma, option_type)
            iv  = calculate_iv(market_price, S, K, T, r, option_type) if market_price else sigma
            rho = _rho(S, K, T, r, sigma, option_type)

            return Greeks(
                delta = round(bs.delta(), 6),
                gamma = round(bs.gamma(), 6),
                theta = round(bs.theta(), 6),
                vega  = round(bs.vega(),  6),
                rho   = round(rho,        6),
                iv    = round(iv * 100,   4),   # return as percentage
                price = round(bs.price(), 2),
            )
        except Exception as e:
            logger.error(f"Greeks calculation error: {e}")
            return Greeks(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    @staticmethod
    def batch(
        S           : float,
        strikes     : list[float],
        T           : float,
        r           : float,
        sigma       : float,
        option_type : str,
    ) -> list[dict]:
        """Calculate Greeks for multiple strikes at once."""
        results = []
        for K in strikes:
            g = GreeksEngine.calculate(S, K, T, r, sigma, option_type)
            results.append({"strike": K, **g.to_dict()})
        return results
