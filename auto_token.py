import hmac, hashlib, struct, time, base64, os
import requests
from urllib.parse import parse_qs, urlparse
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("FYERS_APP_ID", "")
SECRET_KEY = os.getenv("FYERS_SECRET_KEY", "")
REDIRECT = os.getenv("REDIRECT_URL", "http://127.0.0.1:8080/")
TOTP_KEY = os.getenv("FYERS_TOTP_KEY", "")
FY_ID = os.getenv("FYERS_CLIENT_ID", "")
PIN = os.getenv("FYERS_PIN", "")

def generate_totp(secret, interval=30, digits=6):
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    counter = int(time.time() // interval)
    counter_bytes = struct.pack(">Q", counter)
    hmac_hash = hmac.new(key, counter_bytes, hashlib.sha1).digest()
    offset = hmac_hash[-1] & 0x0F
    truncated = hmac_hash[offset:offset+4]
    code = struct.unpack(">I", truncated)[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)

BASE_V2 = "https://api-t2.fyers.in/vagator/v2"
BASE_V3 = "https://api-t1.fyers.in/api/v3"
URL_SEND_LOGIN_OTP = BASE_V2 + "/send_login_otp_v2"
URL_VERIFY_TOTP = BASE_V2 + "/verify_otp"
URL_VERIFY_PIN = BASE_V2 + "/verify_pin_v2"

def auto_login_get_access_token():
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    import base64
    fy_id_enc = base64.b64encode(FY_ID.encode("ascii")).decode("ascii")
    payload = {"fy_id": fy_id_enc, "app_id": "2"}
    r = requests.post(URL_SEND_LOGIN_OTP, json=payload, headers=headers, timeout=15)
    data = r.json()
    if data.get("s") != "ok":
        raise Exception(f"send_login_otp failed: {data}")
    request_key = data["request_key"]
    print("Step 1: Got request_key for OTP")

    totp_code = generate_totp(TOTP_KEY)
    print(f"Generated TOTP: {totp_code}")
    payload = {"request_key": request_key, "otp": totp_code}
    r = requests.post(URL_VERIFY_TOTP, json=payload, headers=headers, timeout=15)
    data = r.json()
    if data.get("s") != "ok":
        raise Exception(f"verify_otp TOTP failed: {data}")
    request_key_2 = data["request_key"]
    print("Step 2: TOTP verified")

    pin_enc = base64.b64encode(PIN.encode("ascii")).decode("ascii")
    payload = {"request_key": request_key_2, "identity_type": "pin", "identifier": pin_enc}
    r = requests.post(URL_VERIFY_PIN, json=payload, headers=headers, timeout=15)
    data = r.json()
    if data.get("s") != "ok":
        raise Exception(f"verify_pin failed: {data}")
    internal_token = data["data"]["access_token"]
    print("Step 3: PIN verified, got internal token")
    return internal_token

def exchange_for_authcode(internal_token):
    headers = {"Authorization": f"Bearer {internal_token}", "Content-Type": "application/json"}
    payload = {
        "fyers_id": FY_ID,
        "app_id": APP_ID.split("-")[0],
        "redirect_uri": REDIRECT,
        "appType": "100",
        "code_challenge": "",
        "state": "sample",
        "scope": "",
        "nonce": "",
        "response_type": "code",
        "create_cookie": True,
    }
    r = requests.post(BASE_V3 + "/token", json=payload, headers=headers, timeout=15)
    data = r.json()
    if data.get("s") != "ok":
        raise Exception(f"authcode exchange failed: {data}")
    redirect_url = data["Url"]
    parsed = urlparse(redirect_url)
    auth_code = parse_qs(parsed.query)["auth_code"][0]
    print("Step 4: Got auth_code silently")
    return auth_code

def get_final_access_token(auth_code):
    import hashlib as hl
    app_hash = hl.sha256(f"{APP_ID}:{SECRET_KEY}".encode()).hexdigest()
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": app_hash,
        "code": auth_code,
    }
    r = requests.post("https://api-t1.fyers.in/api/v3/validate-authcode", json=payload, timeout=15)
    response = r.json()
    token = response.get("access_token", "")
    if not token:
        raise Exception(f"Token generation failed: {response}")
    print("Step 5: Final API access_token obtained")
    return token

def save_token_to_env(token):
    env_path = ".env"
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = [l for l in f.readlines() if not l.startswith("FYERS_ACCESS_TOKEN")]
    lines.append(f"FYERS_ACCESS_TOKEN={token}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)
    print("Step 6: Token saved to .env")

def main():
    print("=" * 50)
    print("  Fyers Auto Token Generator")
    print("=" * 50)
    missing = [k for k, v in {
        "FYERS_APP_ID": APP_ID, "FYERS_SECRET_KEY": SECRET_KEY,
        "FYERS_TOTP_KEY": TOTP_KEY, "FYERS_CLIENT_ID": FY_ID,
        "FYERS_PIN": PIN,
    }.items() if not v]
    if missing:
        print("Missing in .env:", ", ".join(missing))
        return
    try:
        internal_token = auto_login_get_access_token()
        auth_code = exchange_for_authcode(internal_token)
        api_token = get_final_access_token(auth_code)
        save_token_to_env(api_token)
        print("=" * 50)
        print("  SUCCESS - Token renewed!")
        print("  Restart server.py now.")
        print("=" * 50)
    except Exception as e:
        print("Error:", str(e))

if __name__ == "__main__":
    main()
