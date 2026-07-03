#!/bin/bash
cd ~/TradePro && python server.py &
sleep 2
cd ~/tradepro-app && npm start
