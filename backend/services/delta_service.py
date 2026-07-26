"""
TradePro Backend - Delta Exchange India integration (crypto options: BTC, ETH)

Uses ONLY public market-data endpoints (/v2/tickers) - no API key/secret
required. Delta Exchange only requires authentication for placing orders or
reading account/position data, not for reading live option-chain data.

Output is normalized into the exact same shape MarketDataService.get_option_chain()
returns for Fyers (data.optionsChain = [{strike_price, option_type, ltp, bid,
ask, oi, oich, volume, iv, delta, gamma, theta, vega}, ...]) so that
chain_archive.save_snapshot() persists crypto snapshots into the SAME SQLite
table/schema used for NIFTY/BANKNIFTY - same fast simulator queries, same
compact on-disk format, no new storage system needed.

Compatible with Python 3.11+, Termux, Linux. Only dependency: requests.
"""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.india.delta.exchange"
_HEADERS = {"Accept": "application/json", "User-Agent": "TradePro/1.0 python-requests"}
_TIMEOUT = 10

SUPPORTED_UNDERLYINGS = ("BTC", "ETH")

# No cap by default - archive EVERY live expiry, same as NIFTY/BANKNIFTY, so
# BTC/ETH get equally complete data (all expiries, all strikes, bid/ask/OI/
# greeks). Crypto expires daily so this grows the DB faster than the NSE
# indices; keep an eye on size via GET /api/optionchain/archive/stats and
# pass a specific max_expiries if it needs trimming later.
MAX_ARCHIVED_EXPIRIES = None


def _get(path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{BASE_URL}{path}", params=params or {}, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _parse_expiry_from_symbol(symbol: str) -> str | None:
    """
    Delta option symbols look like C-BTC-90000-310125 (DDMMYY at the end).
    Returns YYYY-MM-DD, or None if it doesn't parse as expected.
    """
    try:
        raw = symbol.rsplit("-", 1)[-1]
        return datetime.strptime(raw, "%d%m%y").strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        return None


def get_expiries(underlying: str, max_expiries: int | None = MAX_ARCHIVED_EXPIRIES) -> list[str]:
    """
    Returns every live expiry date (YYYY-MM-DD) that currently has BTC/ETH
    option contracts on Delta Exchange, soonest first. Pass max_expiries to
    cap the count (e.g. for the live-chain-picker UI); leave it None (default)
    to get ALL of them, which is what the 5-min archiver uses so crypto is
    archived just as fully as NIFTY/BANKNIFTY.
    """
    underlying = underlying.upper()
    if underlying not in SUPPORTED_UNDERLYINGS:
        return []
    try:
        data = _get("/v2/tickers", {
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": underlying,
        })
        symbols = {item.get("symbol", "") for item in data.get("result", [])}
        expiries = sorted({d for d in (_parse_expiry_from_symbol(s) for s in symbols) if d})
        return expiries[:max_expiries] if max_expiries else expiries
    except Exception as e:
        logger.warning(f"delta_service.get_expiries({underlying}) failed: {e}")
        return []


def get_option_chain(underlying: str, expiry_date: str) -> dict:
    """
    Fetch the full CE/PE option chain for `underlying` (BTC/ETH) expiring on
    `expiry_date` (YYYY-MM-DD). Response shape mirrors Fyers' live optionsChain
    format so it can be saved via chain_archive.save_snapshot() unchanged.
    """
    underlying = underlying.upper()
    if underlying not in SUPPORTED_UNDERLYINGS:
        return {"success": False, "mock": False, "data": {}}

    try:
        expiry_ddmmyyyy = datetime.strptime(expiry_date, "%Y-%m-%d").strftime("%d-%m-%Y")
        data = _get("/v2/tickers", {
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": underlying,
            "expiry_date": expiry_ddmmyyyy,
        })
        tickers = data.get("result", [])
        if not tickers:
            return {"success": False, "mock": False, "data": {}}

        rows = []
        spot = 0.0
        for t in tickers:
            sp = t.get("spot_price")
            if sp:
                spot = float(sp)

            strike = t.get("strike_price")
            if strike is None:
                continue

            greeks = t.get("greeks") or {}
            quotes = t.get("quotes") or {}
            option_type = "CE" if t.get("contract_type") == "call_options" else "PE"

            def _f(v):
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            rows.append({
                "strike_price": float(strike),
                "option_type" : option_type,
                "ltp"         : _f(t.get("close")) or _f(t.get("mark_price")) or 0,
                "bid"         : _f(quotes.get("best_bid")) or 0,
                "ask"         : _f(quotes.get("best_ask")) or 0,
                "oi"          : _f(t.get("oi")) or 0,
                "oich"        : None,   # Delta doesn't expose OI-change directly
                "volume"      : _f(t.get("volume")) or 0,
                # mark_vol is the implied vol used to compute mark price - closest
                # single-number equivalent to Fyers' per-strike "iv" field
                "iv"          : (_f(t.get("mark_vol")) or 0) * 100 if t.get("mark_vol") else None,
                "delta"       : _f(greeks.get("delta")),
                "gamma"       : _f(greeks.get("gamma")),
                "theta"       : _f(greeks.get("theta")),
                "vega"        : _f(greeks.get("vega")),
            })

        # A blank-option_type row carries the underlying spot price, matching
        # the shape chain_archive._normalize_rows() expects from Fyers' live feed.
        rows.append({"option_type": "", "ltp": spot})

        return {
            "success": True,
            "mock"   : False,
            "spot"   : spot,
            "data"   : {"optionsChain": rows},
        }
    except Exception as e:
        logger.warning(f"delta_service.get_option_chain({underlying}, {expiry_date}) failed: {e}")
        return {"success": False, "mock": False, "data": {}}
