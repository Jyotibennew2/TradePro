"""
TradePro Backend - Request Validators
Validate incoming request parameters.
Compatible with Python 3.11+, Termux, Linux.
"""

from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Valid values
# ---------------------------------------------------------------------------

VALID_SYMBOLS   : set[str] = {"NIFTY", "BANKNIFTY", "MIDCPNIFTY"}
VALID_STRATEGIES: set[str] = {"straddle", "strangle", "ironCondor", "longCall", "longPut"}
MIN_STRIKE_COUNT: int      = 1
MAX_STRIKE_COUNT: int      = 20


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------

def validate_symbol(symbol: Optional[str]) -> Tuple[bool, str]:
    """Validate option chain symbol."""
    if not symbol:
        return False, "symbol is required"
    if symbol.upper() not in VALID_SYMBOLS:
        return False, f"symbol must be one of {sorted(VALID_SYMBOLS)}"
    return True, ""


def validate_expiry(expiry: Optional[str]) -> Tuple[bool, str]:
    """Validate expiry — empty string is allowed (means nearest expiry)."""
    if expiry is None:
        return False, "expiry parameter is required"
    # Empty string = nearest expiry, which is valid
    if expiry and not expiry.strip().lstrip("-").isdigit():
        return False, "expiry must be a unix timestamp string or empty"
    return True, ""


def validate_strike_count(value: Optional[str]) -> Tuple[bool, str]:
    """Validate strikecount parameter."""
    if value is None:
        return True, ""   # optional — default used
    try:
        count = int(value)
    except (ValueError, TypeError):
        return False, "strikecount must be an integer"
    if not (MIN_STRIKE_COUNT <= count <= MAX_STRIKE_COUNT):
        return False, f"strikecount must be between {MIN_STRIKE_COUNT} and {MAX_STRIKE_COUNT}"
    return True, ""


def validate_quantity(value: Optional[any]) -> Tuple[bool, str]:
    """Validate order quantity."""
    if value is None:
        return False, "quantity is required"
    try:
        qty = int(value)
    except (ValueError, TypeError):
        return False, "quantity must be an integer"
    if qty <= 0:
        return False, "quantity must be greater than 0"
    if qty > 10000:
        return False, "quantity must be less than 10000"
    return True, ""


def validate_price(value: Optional[any]) -> Tuple[bool, str]:
    """Validate order price."""
    if value is None:
        return False, "price is required"
    try:
        price = float(value)
    except (ValueError, TypeError):
        return False, "price must be a number"
    if price < 0:
        return False, "price must be >= 0 (0 = market order)"
    if price > 100000:
        return False, "price seems too high, max 100000"
    return True, ""


def validate_strategy(value: Optional[str]) -> Tuple[bool, str]:
    """Validate backtest strategy."""
    if not value:
        return False, "strategy is required"
    if value not in VALID_STRATEGIES:
        return False, f"strategy must be one of {sorted(VALID_STRATEGIES)}"
    return True, ""


def validate_days(value: Optional[any]) -> Tuple[bool, str]:
    """Validate backtest days."""
    if value is None:
        return True, ""   # optional
    try:
        days = int(value)
    except (ValueError, TypeError):
        return False, "days must be an integer"
    if not (1 <= days <= 365):
        return False, "days must be between 1 and 365"
    return True, ""
