#!/bin/bash
echo "=================================================="
echo "  🚀 TradePro Daily Startup"
echo "=================================================="

echo ""
echo "Step 1/3: Renewing Fyers token..."
cd ~/TradePro
python3 auto_token.py

echo ""
echo "Step 2/3: Starting backend server..."
pkill -f server.py 2>/dev/null
sleep 1
cd ~/TradePro
python server.py &
sleep 2

echo ""
echo "Step 3/3: Starting React frontend..."
pkill -f "react-scripts start" 2>/dev/null
sleep 1
cd ~/tradepro-app
npm start &
sleep 5

echo ""
echo "=================================================="
echo "  ✅ ALL READY!"
echo "  Backend:  http://192.0.0.4:8000/api/health"
echo "  Frontend: http://192.0.0.4:3000"
echo "=================================================="
wait
