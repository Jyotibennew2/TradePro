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
import struct
import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

from backend.config import APP_ID, SECRET, TOKEN, REDIRECT_URL


# ---------------------------------------------------------------------------
# TOTP (pure Python, no pyotp)
# ---------------------------------------------------------------------------

def _totp(secret: str) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int(time.time()) // 30)
    mac = hmac.new(key, counter, "sha1").digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1000000).zfill(6)


# ---------------------------------------------------------------------------
# HTTP helper (no requests dependency)
# ---------------------------------------------------------------------------

def _http(url: str, payload: Optional[dict] = None, headers: Optional[dict] = None) -> dict:
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if payload else "GET")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.reason, "code": e.code}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Fyers Service
# ---------------------------------------------------------------------------

class FyersService:
    """
    All Fyers API interactions in one place.
    Instantiate once; call methods as needed.
    """

    BASE = "https://api-t1.fyers.in/api/v3"
    LOGIN_BASE = "https://api-t2.fyers.in/vagator/v2"

    def __init__(
        self,
        app_id: str = APP_ID,
        secret: str = SECRET,
        token: str = TOKEN,
        redirect_url: str = REDIRECT_URL,
    ) -> None:
        self.app_id = app_id
        self.secret = secret
        self.token = token
        self.redirect_url = redirect_url
        self._client = None
        self._init_client()

    # ------------------------------------------------------------------
    # Internal: Fyers SDK client
    # ------------------------------------------------------------------

    def _init_client(self) -> None:
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
        except Exception:
            self._client = None

    # ------------------------------------------------------------------
    # Auth status
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        if not self._client:
            return False
        try:
            resp = self._client.get_profile()
            return resp.get("code") == 200 or resp.get("s") == "ok"
        except Exception:
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
            fy_id_b64 = base64.b64encode(client_id.encode()).decode()
            r1 = _http(f"{self.LOGIN_BASE}/send_login_otp_v2",
                       {"fy_id": fy_id_b64, "app_id": "2"})
            if "request_key" not in r1:
                return {"success": False, "error": f"OTP send failed: {r1}"}
            request_key = r1["request_key"]

            # Step 2: Verify TOTP
            totp_code = _totp(totp_key)
            r2 = _http(f"{self.LOGIN_BASE}/verify_otp",
                       {"request_key": request_key, "identity_type": "totp", "identifier": totp_code})
            if "request_key" not in r2:
                return {"success": False, "error": f"TOTP verify failed: {r2}"}
            request_key2 = r2["request_key"]

            # Step 3: Verify PIN
            pin_b64 = base64.b64encode(pin.encode()).decode()
            r3 = _http(f"{self.LOGIN_BASE}/verify_pin_v2",
                       {"request_key": request_key2, "identity_type": "pin", "identifier": pin_b64})
            if "data" not in r3 or "access_token" not in r3.get("data", {}):
                return {"success": False, "error": f"PIN verify failed: {r3}"}
            access_token = r3["data"]["access_token"]

            # Step 4: Get auth code
            r4 = _http(
                "https://api-t2.fyers.in/api/v3/token",
                {
                    "fyers_id": client_id,
                    "app_id": self.app_id.split("-")[0],
                    "redirect_uri": self.redirect_url,
                    "appType": "100",
                    "code_challenge": "",
                    "state": "tradepro",
                    "scope": "",
                    "nonce": "",
                    "response_type": "code",
                    "create_cookie": True,
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            auth_code = r4.get("data", {}).get("auth", "")
            if not auth_code:
                return {"success": False, "error": f"Auth code missing: {r4}"}

            # Step 5: Validate auth code
            app_hash = hashlib.sha256(f"{self.app_id}:{self.secret}".encode()).hexdigest()
            r5 = _http(
                f"{self.BASE}/validate-authcode",
                {"grant_type": "authorization_code", "appIdHash": app_hash, "code": auth_code},
            )
            new_token = r5.get("access_token", "")
            if not new_token:
                return {"success": False, "error": f"Token exchange failed: {r5}"}

            self.token = new_token
            self._init_client()
            return {"success": True, "token": new_token}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------

    def get_quotes(self, symbols: str = "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX") -> dict:
        """
        Returns {"success": True, "data": {symbol: {ltp, ch, chp}}, "mock": False}
        """
        if self._client:
            try:
                resp = self._client.quotes({"symbols": symbols})
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    data = {}
                    for item in resp.get("d", []):
                        v = item.get("v", {})
                        sym = v.get("symbol") or item.get("n", "")
                        data[sym] = {
                            "ltp": v.get("lp", 0),
                            "ch":  v.get("ch", 0),
                            "chp": v.get("chp", 0),
                            "open": v.get("open_price", 0),
                            "high": v.get("high_price", 0),
                            "low":  v.get("low_price", 0),
                            "close": v.get("prev_close_price", 0),
                        }
                    return {"success": True, "data": data, "mock": False}
            except Exception as e:
                print("Quotes error:", e)

        # Mock fallback
        t = time.time()
        return {
            "success": True,
            "mock": True,
            "data": {
                "NSE:NIFTY50-INDEX":    {"ltp": round(24300 + math.sin(t / 20) * 25 + (t % 10) - 5, 2), "ch": 120.5,  "chp": 0.50},
                "NSE:NIFTYBANK-INDEX":  {"ltp": round(58000 + math.sin(t / 25) * 30 + (t % 10) - 5, 2), "ch": -120.5, "chp": -0.25},
                "NSE:NIFTYMID100-INDEX":{"ltp": round(12800 + math.sin(t / 22) * 15, 2),               "ch": 45.0,   "chp": 0.35},
            },
        }

    # ------------------------------------------------------------------
    # Option Chain
    # ------------------------------------------------------------------

    def get_option_chain(self, symbol: str = "NIFTY", expiry: str = "", strike_count: int = 10) -> dict:
        """
        symbol: 'NIFTY' or 'BANKNIFTY'
        Returns {"success": True, "data": {...}, "spot": float, "mock": bool}
        """
        symbol_map = {
            "NIFTY":      "NSE:NIFTY50-INDEX",
            "BANKNIFTY":  "NSE:NIFTYBANK-INDEX",
            "MIDCPNIFTY": "NSE:NIFTYMID100-INDEX",
        }
        fyers_symbol = symbol_map.get(symbol.upper(), symbol)

        if self._client:
            try:
                payload = {"symbol": fyers_symbol, "strikecount": strike_count, "timestamp": ""}
                if expiry:
                    payload["timestamp"] = expiry
                resp = self._client.optionchain(payload)
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    return {"success": True, "data": resp.get("data", {}), "mock": False}
            except Exception as e:
                print("Option chain error:", e)

        # Mock fallback
        return self._mock_option_chain(fyers_symbol, strike_count)

    def _mock_option_chain(self, symbol: str, count: int) -> dict:
        from backend.pricing import bs
        base = {"NSE:NIFTY50-INDEX": 24300, "NSE:NIFTYBANK-INDEX": 58000, "NSE:NIFTYMID100-INDEX": 12800}
        import random, hashlib as _h
        spot = round(base.get(symbol, 24300) + math.sin(time.time() / 30) * base.get(symbol, 24300) * 0.001, 2)
        step = 100 if spot < 30000 else 200
        atm  = round(spot / step) * step
        rows = []
        for i in range(-count, count + 1):
            K    = atm + i * step
            ce   = round(bs(spot, K, 30 / 365, 0.065, 0.14, "call"), 2)
            pe   = round(bs(spot, K, 30 / 365, 0.065, 0.14, "put"),  2)
            oi   = max(0.05, 1 - abs(K - spot) / (spot * 0.12)) * 1200000
            h    = int(_h.md5(str(K).encode()).hexdigest(), 16) % 1000 / 1000
            skew = round(14 + (1.5 if K < spot else -1) * (abs(K - spot) / spot) * 80, 1)
            rows.append({
                "strike": K, "ce_ltp": ce, "pe_ltp": pe,
                "ce_oi": int(oi * (0.7 + 0.6 * h)),       "pe_oi": int(oi * (0.7 + 0.6 * (1 - h))),
                "ce_vol": int(oi * 0.4 * h),               "pe_vol": int(oi * 0.4 * (1 - h)),
                "ce_iv": skew, "pe_iv": skew,
                "ce_delta": round(0.5 - (K - spot) / (spot * 0.3), 3),
                "pe_delta": round(-0.5 - (K - spot) / (spot * 0.3), 3),
                "atm": K == atm,
            })
        return {
            "success": True, "mock": True, "spot": spot,
            "data": {"expiryData": rows, "atmIndex": count},
        }

    # ------------------------------------------------------------------
    # Funds
    # ------------------------------------------------------------------

    def get_funds(self) -> dict:
        if self._client:
            try:
                resp = self._client.funds()
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    return {"success": True, "mock": False, "data": resp.get("fund_limit", [])}
            except Exception as e:
                print("Funds error:", e)
        return {"success": True, "mock": True, "data": []}

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def get_orders(self) -> dict:
        if self._client:
            try:
                resp = self._client.orderbook()
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    return {"success": True, "mock": False, "data": resp.get("orderBook", [])}
            except Exception as e:
                print("Orders error:", e)
        return {"success": True, "mock": True, "data": []}

    def place_order(self, order: dict) -> dict:
        if self._client:
            try:
                resp = self._client.place_order(order)
                return {"success": True, "mock": False, "data": resp}
            except Exception as e:
                return {"success": False, "error": str(e)}
        return {"success": False, "mock": True, "error": "Not authenticated"}

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> dict:
        if self._client:
            try:
                resp = self._client.positions()
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    return {"success": True, "mock": False, "data": resp.get("netPositions", [])}
            except Exception as e:
                print("Positions error:", e)
        return {"success": True, "mock": True, "data": []}
