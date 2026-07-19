"""
TradePro Backend - Fyers Service
All Fyers API logic in one service class.
Compatible with Python 3.11+, Termux, Linux.
"""

import hashlib
import hmac
import time
import base64
import math
import random
import struct
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from typing import Optional

from backend.config import APP_ID, SECRET, TOKEN, REDIRECT_URL
from backend.pricing import bs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Symbol map
# ---------------------------------------------------------------------------

SYMBOL_MAP: dict[str, str] = {
    "NIFTY"     : "NSE:NIFTY50-INDEX",
    "BANKNIFTY" : "NSE:NIFTYBANK-INDEX",
    "MIDCPNIFTY": "NSE:NIFTYMID100-INDEX",
}

BASE_PRICES: dict[str, float] = {
    "NSE:NIFTY50-INDEX"    : 24300.0,
    "NSE:NIFTYBANK-INDEX"  : 58000.0,
    "NSE:NIFTYMID100-INDEX": 12800.0,
}

# ---------------------------------------------------------------------------
# TOTP (pure Python, no pyotp)
# ---------------------------------------------------------------------------

def _totp(secret: str) -> str:
    """Generate TOTP code from base32 secret."""
    key     = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int(time.time()) // 30)
    mac     = hmac.new(key, counter, "sha1").digest()
    offset  = mac[-1] & 0x0F
    code    = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


# ---------------------------------------------------------------------------
# HTTP helper (no requests dependency)
# ---------------------------------------------------------------------------

def _http(
    url    : str,
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = 10,
) -> dict:
    """Minimal HTTP client using stdlib only."""
    data = json.dumps(payload).encode() if payload else None
    req  = urllib.request.Request(
        url, data=data,
        headers=headers or {},
        method="POST" if payload else "GET",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        logger.error(f"HTTP {e.code} on {url}: {e.reason}")
        return {"error": e.reason, "code": e.code}
    except Exception as e:
        logger.error(f"HTTP error on {url}: {e}")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Fyers Service
# ---------------------------------------------------------------------------

class FyersService:
    """
    All Fyers API interactions in one place.
    Instantiate once and reuse across requests.
    """

    _BASE      = "https://api-t1.fyers.in/api/v3"
    _LOGIN_BASE= "https://api-t2.fyers.in/vagator/v2"

    def __init__(
        self,
        app_id      : str = APP_ID,
        secret      : str = SECRET,
        token       : str = TOKEN,
        redirect_url: str = REDIRECT_URL,
    ) -> None:
        self.app_id       = app_id
        self.secret       = secret
        self.token        = token
        self.redirect_url = redirect_url
        self._client      = None
        self._init_client()

    # ------------------------------------------------------------------
    # Internal: SDK client
    # ------------------------------------------------------------------

    def _init_client(self) -> None:
        """Initialize Fyers SDK client if token is available."""
        if not (self.app_id and self.token):
            self._client = None
            return
        try:
            from fyers_apiv3 import fyersModel
            self._client = fyersModel.FyersModel(
                client_id=self.app_id,
                token=self.token,
                log_path="",
            )
            logger.info("Fyers client initialized successfully")
        except Exception as e:
            logger.error(f"Fyers client init failed: {e}")
            self._client = None

    # ------------------------------------------------------------------
    # Auth status
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        """Check if Fyers client is authenticated."""
        if not self._client:
            return False
        try:
            resp = self._client.get_profile()
            return resp.get("code") == 200 or resp.get("s") == "ok"
        except Exception as e:
            logger.warning(f"Auth check failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Auto login (TOTP flow)
    # ------------------------------------------------------------------

    def auto_login(self, client_id: str, pin: str, totp_key: str) -> dict:
        """
        Fully automated token generation using TOTP.
        Returns {"success": True, "token": "..."} or {"success": False, "error": "..."}
        """
        try:
            # Step 1: Send OTP
            logger.info("Step 1: Sending login OTP")
            fy_id_b64 = base64.b64encode(client_id.encode()).decode()
            r1 = _http(f"{self._LOGIN_BASE}/send_login_otp_v2",
                       {"fy_id": fy_id_b64, "app_id": "2"})
            if "request_key" not in r1:
                return {"success": False, "error": f"OTP send failed: {r1}"}
            request_key = r1["request_key"]

            # Step 2: Verify TOTP
            logger.info("Step 2: Verifying TOTP")
            totp_code = _totp(totp_key)
            r2 = _http(f"{self._LOGIN_BASE}/verify_otp",
                       {"request_key": request_key, "identity_type": "totp", "identifier": totp_code})
            if "request_key" not in r2:
                return {"success": False, "error": f"TOTP verify failed: {r2}"}
            request_key2 = r2["request_key"]

            # Step 3: Verify PIN
            logger.info("Step 3: Verifying PIN")
            pin_b64 = base64.b64encode(pin.encode()).decode()
            r3 = _http(f"{self._LOGIN_BASE}/verify_pin_v2",
                       {"request_key": request_key2, "identity_type": "pin", "identifier": pin_b64})
            if "data" not in r3 or "access_token" not in r3.get("data", {}):
                return {"success": False, "error": f"PIN verify failed: {r3}"}
            access_token = r3["data"]["access_token"]

            # Step 4: Get auth code
            logger.info("Step 4: Getting auth code")
            r4 = _http(
                "https://api-t2.fyers.in/api/v3/token",
                {
                    "fyers_id"      : client_id,
                    "app_id"        : self.app_id.split("-")[0],
                    "redirect_uri"  : self.redirect_url,
                    "appType"       : "100",
                    "code_challenge": "",
                    "state"         : "tradepro",
                    "scope"         : "",
                    "nonce"         : "",
                    "response_type" : "code",
                    "create_cookie" : True,
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            auth_code = r4.get("data", {}).get("auth", "")
            if not auth_code:
                return {"success": False, "error": f"Auth code missing: {r4}"}

            # Step 5: Validate auth code
            logger.info("Step 5: Validating auth code")
            app_hash = hashlib.sha256(f"{self.app_id}:{self.secret}".encode()).hexdigest()
            r5 = _http(
                f"{self._BASE}/validate-authcode",
                {"grant_type": "authorization_code", "appIdHash": app_hash, "code": auth_code},
            )
            new_token = r5.get("access_token", "")
            if not new_token:
                return {"success": False, "error": f"Token exchange failed: {r5}"}

            self.token = new_token
            self._init_client()
            logger.info("Auto login successful")
            return {"success": True, "token": new_token}

        except Exception as e:
            logger.exception(f"Auto login failed: {e}")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------

    def get_quotes(self, symbols: str = "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX") -> dict:
        """Returns live or mock quotes."""
        if self._client:
            try:
                resp = self._client.quotes({"symbols": symbols})
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    data: dict = {}
                    for item in resp.get("d", []):
                        v   = item.get("v", {})
                        sym = v.get("symbol") or item.get("n", "")
                        data[sym] = {
                            "ltp"  : v.get("lp", 0),
                            "ch"   : v.get("ch", 0),
                            "chp"  : v.get("chp", 0),
                            "open" : v.get("open_price", 0),
                            "high" : v.get("high_price", 0),
                            "low"  : v.get("low_price", 0),
                            "close": v.get("prev_close_price", 0),
                            "vol"  : v.get("volume", 0),
                            "oi"   : v.get("oi", 0),
                        }
                    return {"success": True, "data": data, "mock": False}
            except Exception as e:
                logger.error(f"Quotes error: {e}")

        # Mock fallback
        t = time.time()
        return {
            "success": True,
            "mock"   : True,
            "data"   : {
                "NSE:NIFTY50-INDEX"    : {"ltp": round(24300 + math.sin(t / 20) * 25 + (t % 10) - 5, 2), "ch": 120.5,  "chp":  0.50},
                "NSE:NIFTYBANK-INDEX"  : {"ltp": round(58000 + math.sin(t / 25) * 30 + (t % 10) - 5, 2), "ch": -120.5, "chp": -0.25},
                "NSE:NIFTYMID100-INDEX": {"ltp": round(12800 + math.sin(t / 22) * 15, 2),                 "ch":  45.0,  "chp":  0.35},
            },
        }

    # ------------------------------------------------------------------
    # Historical candles
    # ------------------------------------------------------------------

    def get_history(self, symbol: str, days: int = 90, resolution: str = "D") -> dict:
        """Returns live Fyers historical candles, or realistic mock candles."""
        fyers_symbol = SYMBOL_MAP.get(symbol.upper(), symbol)

        if self._client:
            try:
                to_ts   = int(time.time())
                from_ts = to_ts - days * 86400
                payload = {
                    "symbol"     : fyers_symbol,
                    "resolution" : resolution,
                    "date_format": "0",
                    "range_from" : str(from_ts),
                    "range_to"   : str(to_ts),
                    "cont_flag"  : "1",
                }
                resp = self._client.history(payload)
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    raw = resp.get("candles", [])
                    if raw:
                        candles = [
                            {"t": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]}
                            for c in raw
                        ]
                        return {"success": True, "mock": False, "candles": candles}
            except Exception as e:
                logger.error(f"History error: {e}")

        return self._mock_history(fyers_symbol, days, resolution)

    def _mock_history(self, symbol: str, days: int, resolution: str = "D") -> dict:
        """
        Generate realistic mock candles (random walk) when live data unavailable.
        resolution: "D" for daily candles, or minutes as a string ("5","15","30","60","120")
        for intraday candles — spaced accordingly instead of one-per-day.
        """
        base    = BASE_PRICES.get(symbol, 24300.0)
        price   = base * 0.90
        candles: list = []
        now     = int(time.time())

        if resolution == "D":
            step_seconds   = 86400
            candle_count   = days
            vol_per_candle = (500000, 2000000)
        else:
            minutes         = int(resolution)
            step_seconds    = minutes * 60
            # ~6.25 trading hours/day (375 min) worth of candles per day
            candles_per_day = max(1, 375 // minutes)
            candle_count    = days * candles_per_day
            vol_per_candle  = (5000, 50000)

        for i in range(candle_count, -1, -1):
            price  *= (1 + (random.random() - 0.49) * (0.015 if resolution == "D" else 0.004))
            high    = price * (1 + random.random() * (0.008 if resolution == "D" else 0.002))
            low     = price * (1 - random.random() * (0.008 if resolution == "D" else 0.002))
            open_   = price * (1 + (random.random() - 0.5) * (0.005 if resolution == "D" else 0.0015))
            volume  = int(random.uniform(*vol_per_candle))
            candles.append({
                "t"     : now - i * step_seconds,
                "open"  : round(open_, 2),
                "high"  : round(high,  2),
                "low"   : round(low,   2),
                "close" : round(price, 2),
                "volume": volume,
            })

        return {"success": True, "mock": True, "candles": candles}

    # ------------------------------------------------------------------
    # Available expiries
    # ------------------------------------------------------------------

    def get_expiries(self, symbol: str) -> dict:
        """
        Returns the list of available expiry dates for a symbol's option chain
        (both weekly and monthly contracts, as offered by Fyers). Each item:
        {"expiry": "<unix timestamp string>", "date": "DD-MM-YYYY"}.
        """
        fyers_symbol = SYMBOL_MAP.get(symbol.upper(), symbol)

        if self._client:
            try:
                payload = {"symbol": fyers_symbol, "strikecount": 1, "timestamp": ""}
                resp = self._client.optionchain(payload)
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    expiry_data = resp.get("data", {}).get("expiryData", [])
                    if expiry_data and "strike" not in expiry_data[0]:
                        return {"success": True, "mock": False, "expiries": expiry_data}
            except Exception as e:
                logger.error(f"Expiries error: {e}")

        return self._mock_expiries()

    def _mock_expiries(self) -> dict:
        """Synthetic expiry list — next 4 Thursdays (NSE index-option weekly cycle)."""
        now = datetime.now()
        days_ahead = (3 - now.weekday()) % 7   # Thursday == 3
        if days_ahead == 0 and now.hour >= 15:
            days_ahead = 7
        first = now + timedelta(days=days_ahead)

        expiries = []
        for i in range(4):
            d = first + timedelta(weeks=i)
            expiries.append({
                "date"  : d.strftime("%d-%m-%Y"),
                "expiry": str(int(d.replace(hour=15, minute=30, second=0, microsecond=0).timestamp())),
            })
        return {"success": True, "mock": True, "expiries": expiries}

    # ------------------------------------------------------------------
    # Option Chain
    # ------------------------------------------------------------------

    def get_option_chain(
        self,
        symbol      : str = "NIFTY",
        expiry      : str = "",
        strike_count: int = 10,
    ) -> dict:
        """Returns live or mock option chain."""
        fyers_symbol = SYMBOL_MAP.get(symbol.upper(), symbol)

        if self._client:
            try:
                payload: dict = {"symbol": fyers_symbol, "strikecount": strike_count, "timestamp": expiry or ""}
                resp = self._client.optionchain(payload)
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    return {"success": True, "data": resp.get("data", {}), "mock": False}
            except Exception as e:
                logger.error(f"Option chain error: {e}")

        return self._mock_option_chain(fyers_symbol, strike_count)

    def _mock_option_chain(self, symbol: str, count: int) -> dict:
        """
        Generate realistic mock option chain data — including bid/ask spread,
        volume, and OI-change so downstream archiving/backtesting has the same
        fields available in MOCK mode as in LIVE mode (Fyers returns
        bid/ask/volume/oich per contract: strike_price, option_type, ltp,
        ltpch, bid, ask, oi, oich, volume).
        """
        import hashlib as _h
        base  = BASE_PRICES.get(symbol, 24300.0)
        spot  = round(base + math.sin(time.time() / 30) * base * 0.001, 2)
        step  = 100 if spot < 30000 else 200
        atm   = round(spot / step) * step
        rows  = []
        for i in range(-count, count + 1):
            K    = atm + i * step
            ce   = round(bs(spot, K, 30 / 365, 0.065, 0.14, "call"), 2)
            pe   = round(bs(spot, K, 30 / 365, 0.065, 0.14, "put"),  2)
            oi   = max(0.05, 1 - abs(K - spot) / (spot * 0.12)) * 1_200_000
            h    = int(_h.md5(str(K).encode()).hexdigest(), 16) % 1000 / 1000
            skew = round(14 + (1.5 if K < spot else -1) * (abs(K - spot) / spot) * 80, 1)

            # Bid/ask spread widens for far OTM strikes (thinner liquidity)
            dist_pct = abs(K - spot) / spot
            spread_pct = 0.004 + dist_pct * 0.03
            ce_spread = max(round(ce * spread_pct, 2), 0.05)
            pe_spread = max(round(pe * spread_pct, 2), 0.05)

            rows.append({
                "strike"  : K,
                "ce_ltp"  : ce,   "pe_ltp"  : pe,
                "ce_bid"  : round(max(ce - ce_spread / 2, 0.05), 2),
                "ce_ask"  : round(ce + ce_spread / 2, 2),
                "pe_bid"  : round(max(pe - pe_spread / 2, 0.05), 2),
                "pe_ask"  : round(pe + pe_spread / 2, 2),
                "ce_oi"   : int(oi * (0.7 + 0.6 * h)),       "pe_oi"  : int(oi * (0.7 + 0.6 * (1 - h))),
                "ce_oich" : int(oi * (0.7 + 0.6 * h) * (random.random() - 0.5) * 0.15),
                "pe_oich" : int(oi * (0.7 + 0.6 * (1 - h)) * (random.random() - 0.5) * 0.15),
                "ce_volume": int(oi * 0.4 * h),               "pe_volume": int(oi * 0.4 * (1 - h)),
                "ce_iv"   : skew, "pe_iv"   : skew,
                "ce_delta": round(0.5 - (K - spot) / (spot * 0.3), 3),
                "pe_delta": round(-0.5 - (K - spot) / (spot * 0.3), 3),
                "atm"     : K == atm,
            })
        return {
            "success": True,
            "mock"   : True,
            "spot"   : spot,
            "data"   : {"expiryData": rows, "atmIndex": count},
        }

    # ------------------------------------------------------------------
    # Funds
    # ------------------------------------------------------------------

    def get_funds(self) -> dict:
        """Returns live or mock funds data."""
        if self._client:
            try:
                resp = self._client.funds()
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    fl    = resp.get("fund_limit", [])
                    total = next((f["equityAmount"] for f in fl if f.get("title") == "Total Balance"), 0)
                    used  = next((f["equityAmount"] for f in fl if f.get("title") == "Utilised Amount"), 0)
                    return {"success": True, "mock": False, "data": {"total": total, "used": used, "available": total - used}}
            except Exception as e:
                logger.error(f"Funds error: {e}")
        return {"success": True, "mock": True, "data": {"total": 500000, "used": 0, "available": 500000}}

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def get_orders(self) -> dict:
        """Returns live or mock orders."""
        if self._client:
            try:
                resp = self._client.orderbook()
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    return {"success": True, "mock": False, "data": resp.get("orderBook", [])}
            except Exception as e:
                logger.error(f"Orders error: {e}")
        return {"success": True, "mock": True, "data": []}

    def place_order(self, order: dict) -> dict:
        """Place a live or mock order."""
        if self._client:
            try:
                resp = self._client.place_order(order)
                return {"success": True, "mock": False, "data": resp}
            except Exception as e:
                logger.error(f"Place order error: {e}")
                return {"success": False, "error": str(e)}
        return {"success": False, "mock": True, "error": "Not authenticated"}

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> dict:
        """Returns live or mock positions."""
        if self._client:
            try:
                resp = self._client.positions()
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    return {"success": True, "mock": False, "data": resp.get("netPositions", [])}
            except Exception as e:
                logger.error(f"Positions error: {e}")
        return {"success": True, "mock": True, "data": []}
