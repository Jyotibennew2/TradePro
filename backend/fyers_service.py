"""
TradePro Backend - Fyers Service
All Fyers API logic in one service class.
Compatible with Python 3.11+, Termux, Linux.

NOTE: Mock/synthetic market data generation has been intentionally removed.
Every method below either returns real Fyers data or a clear error with
"mock": false. No random-walk or reconstructed candles/quotes/chains are
ever generated as a silent fallback. (backend/routes/optionchain/historical
is a separate, explicitly-labeled Black-Scholes reconstruction endpoint
for a *user-supplied* historical spot price — that is unrelated to this
file and unaffected by this change.)
"""

import hashlib
import hmac
import time
import base64
import struct
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from typing import Optional

from backend.config import APP_ID, SECRET, TOKEN, REDIRECT_URL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Symbol map
# ---------------------------------------------------------------------------

SYMBOL_MAP: dict[str, str] = {
    "NIFTY"     : "NSE:NIFTY50-INDEX",
    "BANKNIFTY" : "NSE:NIFTYBANK-INDEX",
    "MIDCPNIFTY": "NSE:NIFTYMID100-INDEX",
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

    Every public get_*() method returns real Fyers data on success, or a
    dict with "success": False, "mock": False, "error": "<reason>" on any
    failure (missing client, Fyers non-success response, or exception).
    Nothing here ever synthesizes market data.
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
        """Returns live Fyers quotes, or a clear error (never synthetic data)."""
        if not self._client:
            return {"success": False, "mock": False, "error": "Fyers client not authenticated (no token)"}
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
            logger.warning(f"Quotes non-success response for {symbols}: {resp}")
            return {
                "success": False, "mock": False,
                "error": resp.get("message", "Fyers quotes call failed"),
                "fyers_code": resp.get("code"), "fyers_status": resp.get("s"),
            }
        except Exception as e:
            logger.error(f"Quotes error: {e}")
            return {"success": False, "mock": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Historical candles
    # ------------------------------------------------------------------

    def get_history(self, symbol: str, days: int = 90, resolution: str = "D") -> dict:
        """Returns real Fyers historical candles, or a clear error (never synthetic candles)."""
        fyers_symbol = SYMBOL_MAP.get(symbol.upper(), symbol)

        if not self._client:
            return {"success": False, "mock": False, "symbol": fyers_symbol,
                    "error": "Fyers client not authenticated (no token)"}

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
                logger.warning(f"History empty candles for {fyers_symbol} ({resolution}, {days}d): {resp}")
                return {"success": False, "mock": False, "symbol": fyers_symbol,
                        "error": "Fyers returned zero candles for this symbol/range",
                        "fyers_response": resp}

            # Fyers returned a response but not a success code — e.g. invalid
            # symbol, unsupported segment/permission, or a rejected request.
            # Surfaced as a real, visible error — never silently mocked.
            logger.warning(f"History non-success response for {fyers_symbol} ({resolution}, {days}d): {resp}")
            return {
                "success": False, "mock": False, "symbol": fyers_symbol,
                "error": resp.get("message", "Fyers history call failed"),
                "fyers_code": resp.get("code"), "fyers_status": resp.get("s"),
            }
        except Exception as e:
            logger.error(f"History error for {fyers_symbol}: {e}")
            return {"success": False, "mock": False, "symbol": fyers_symbol, "error": str(e)}

    # ------------------------------------------------------------------
    # Available expiries
    # ------------------------------------------------------------------

    def get_expiries(self, symbol: str) -> dict:
        """
        Returns the list of available expiry dates for a symbol's option chain
        (both weekly and monthly contracts, as offered by Fyers), or a clear
        error — never a synthetic Thursday-cycle fallback list.
        Each item on success: {"expiry": "<unix timestamp string>", "date": "DD-MM-YYYY"}.
        """
        fyers_symbol = SYMBOL_MAP.get(symbol.upper(), symbol)

        if not self._client:
            return {"success": False, "mock": False, "error": "Fyers client not authenticated (no token)"}

        try:
            payload = {"symbol": fyers_symbol, "strikecount": 1, "timestamp": ""}
            resp = self._client.optionchain(payload)
            if resp.get("code") == 200 or resp.get("s") == "ok":
                expiry_data = resp.get("data", {}).get("expiryData", [])
                if expiry_data and "strike" not in expiry_data[0]:
                    return {"success": True, "mock": False, "expiries": expiry_data}
                logger.warning(f"Expiries: unexpected response shape for {fyers_symbol}: {resp}")
                return {"success": False, "mock": False, "error": "Fyers returned no expiry data", "fyers_response": resp}
            logger.warning(f"Expiries non-success response for {fyers_symbol}: {resp}")
            return {
                "success": False, "mock": False,
                "error": resp.get("message", "Fyers expiries call failed"),
                "fyers_code": resp.get("code"), "fyers_status": resp.get("s"),
            }
        except Exception as e:
            logger.error(f"Expiries error: {e}")
            return {"success": False, "mock": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Option Chain
    # ------------------------------------------------------------------

    def get_option_chain(
        self,
        symbol      : str = "NIFTY",
        expiry      : str = "",
        strike_count: int = 10,
    ) -> dict:
        """Returns real Fyers option chain, or a clear error (never a synthetic Black-Scholes chain)."""
        fyers_symbol = SYMBOL_MAP.get(symbol.upper(), symbol)

        if not self._client:
            return {"success": False, "mock": False, "error": "Fyers client not authenticated (no token)"}

        try:
            payload: dict = {"symbol": fyers_symbol, "strikecount": strike_count, "timestamp": expiry or ""}
            resp = self._client.optionchain(payload)
            if resp.get("code") == 200 or resp.get("s") == "ok":
                return {"success": True, "data": resp.get("data", {}), "mock": False}
            logger.warning(f"Option chain non-success response for {fyers_symbol}: {resp}")
            return {
                "success": False, "mock": False,
                "error": resp.get("message", "Fyers option chain call failed"),
                "fyers_code": resp.get("code"), "fyers_status": resp.get("s"),
            }
        except Exception as e:
            logger.error(f"Option chain error: {e}")
            return {"success": False, "mock": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Funds
    # ------------------------------------------------------------------

    def get_funds(self) -> dict:
        """Returns real Fyers funds data, or a clear error (never synthetic balances)."""
        if not self._client:
            return {"success": False, "mock": False, "error": "Fyers client not authenticated (no token)"}
        try:
            resp = self._client.funds()
            if resp.get("code") == 200 or resp.get("s") == "ok":
                fl    = resp.get("fund_limit", [])
                total = next((f["equityAmount"] for f in fl if f.get("title") == "Total Balance"), 0)
                used  = next((f["equityAmount"] for f in fl if f.get("title") == "Utilised Amount"), 0)
                return {"success": True, "mock": False, "data": {"total": total, "used": used, "available": total - used}}
            logger.warning(f"Funds non-success response: {resp}")
            return {
                "success": False, "mock": False,
                "error": resp.get("message", "Fyers funds call failed"),
                "fyers_code": resp.get("code"), "fyers_status": resp.get("s"),
            }
        except Exception as e:
            logger.error(f"Funds error: {e}")
            return {"success": False, "mock": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def get_orders(self) -> dict:
        """Returns real Fyers order book, or a clear error (never a synthetic empty/fake list)."""
        if not self._client:
            return {"success": False, "mock": False, "error": "Fyers client not authenticated (no token)"}
        try:
            resp = self._client.orderbook()
            if resp.get("code") == 200 or resp.get("s") == "ok":
                return {"success": True, "mock": False, "data": resp.get("orderBook", [])}
            logger.warning(f"Orders non-success response: {resp}")
            return {
                "success": False, "mock": False,
                "error": resp.get("message", "Fyers orderbook call failed"),
                "fyers_code": resp.get("code"), "fyers_status": resp.get("s"),
            }
        except Exception as e:
            logger.error(f"Orders error: {e}")
            return {"success": False, "mock": False, "error": str(e)}

    def place_order(self, order: dict) -> dict:
        """
        Place a live order via Fyers. NOTE (unchanged by this fix, flagged in
        prior audit finding #1, out of scope for this change set): this does
        not yet validate resp.get("code")/resp.get("s") before reporting
        success — that remains a separate, not-yet-implemented fix.
        """
        if self._client:
            try:
                resp = self._client.place_order(order)
                return {"success": True, "mock": False, "data": resp}
            except Exception as e:
                logger.error(f"Place order error: {e}")
                return {"success": False, "mock": False, "error": str(e)}
        return {"success": False, "mock": False, "error": "Fyers client not authenticated (no token)"}

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> dict:
        """Returns real Fyers net positions, or a clear error (never a synthetic empty/fake list)."""
        if not self._client:
            return {"success": False, "mock": False, "error": "Fyers client not authenticated (no token)"}
        try:
            resp = self._client.positions()
            if resp.get("code") == 200 or resp.get("s") == "ok":
                return {"success": True, "mock": False, "data": resp.get("netPositions", [])}
            logger.warning(f"Positions non-success response: {resp}")
            return {
                "success": False, "mock": False,
                "error": resp.get("message", "Fyers positions call failed"),
                "fyers_code": resp.get("code"), "fyers_status": resp.get("s"),
            }
        except Exception as e:
            logger.error(f"Positions error: {e}")
            return {"success": False, "mock": False, "error": str(e)}
