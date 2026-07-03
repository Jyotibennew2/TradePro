#!/bin/bash
cd ~/TradePro && python server.py &
sleep 1
echo "Browser mein kholo:"
python -c "
from dotenv import load_dotenv
import os
load_dotenv()
from fyers_apiv3 import fyersModel
s = fyersModel.SessionModel(client_id=os.getenv('FYERS_APP_ID'),secret_key=os.getenv('FYERS_SECRET_KEY'),redirect_uri='http://127.0.0.1:8080/',response_type='code',grant_type='authorization_code')
print(s.generate_authcode())
"
read -p "Auth Code: " code
curl -s -X POST http://localhost:8000/api/auth/token -H "Content-Type: application/json" -d "{\"auth_code\":\"$code\"}"
echo "Token Done!"
