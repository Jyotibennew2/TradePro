#!/bin/bash
# TradePro Watchdog
#
# Safety net BETWEEN the once-daily daily_start.sh restart (8:45 AM cron +
# on boot via ~/.termux/boot/tradepro-boot.sh). If server.py crashes or
# hangs for any reason OTHER than Fyers token expiry — that case is
# already self-healed in-process by server.py's own auto_relogin_fyers
# scheduler task once the process is running — this brings it back up
# without waiting for the next scheduled daily_start.sh run.
#
# Checks the existing /api/health endpoint (not just whether the process
# exists) so a hung-but-alive process gets restarted too, not just a
# fully-dead one. Only restarts the backend — NOT the frontend (vite) and
# NOT a fresh token renewal, since server.py already handles its own
# token recovery once it's up.
#
# Intended to run frequently via cron (every 5 min) — see the crontab
# line in this repo's README/setup notes for how it's wired in.

APP_DIR="/data/data/com.termux/files/home/TradePro"
LOGFILE="$APP_DIR/watchdog.log"
HEALTH_URL="http://localhost:8000/api/health"

cd "$APP_DIR" || exit 1

if curl -sf --max-time 5 "$HEALTH_URL" > /dev/null 2>&1; then
  # Healthy — say nothing, so the log only fills up with actual events.
  exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') - health check failed at $HEALTH_URL, restarting server.py..." >> "$LOGFILE"

# Clear out any stale/hung process before restarting, same pattern
# daily_start.sh already uses.
pkill -9 -f "server.py" 2>/dev/null
sleep 1

nohup python3 server.py >> "$LOGFILE" 2>&1 &

echo "$(date '+%Y-%m-%d %H:%M:%S') - restart issued (pid $!)" >> "$LOGFILE"
