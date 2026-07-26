#!/data/data/com.termux/files/usr/bin/bash
# TradePro - Daily Google Drive backup for chain_archive.db
#
# Runs once a day (scheduled for 3 AM via cron - see setup instructions in
# scripts/README_backup.md) and copies the option-chain SQLite database
# (NIFTY + BANKNIFTY + BTC + ETH, all expiries) to Google Drive using rclone.
#
# rclone must already be configured with a remote named "gdrive" pointing at
# your own Google account (one-time setup - see README_backup.md). This
# script itself needs no Google credentials of its own; it just calls
# whatever remote rclone already has stored.

set -euo pipefail

# --- Adjust these two paths only if your TradePro folder location differs ---
DB_PATH="$HOME/TradePro/data/archive/chain_archive.db"
LOG_FILE="$HOME/TradePro/data/archive/backup.log"

# Destination folder inside your Google Drive (created automatically if missing)
DRIVE_FOLDER="gdrive:TradePro-Backups"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M")
BACKUP_NAME="chain_archive_${TIMESTAMP}.db"

echo "[$(date)] Starting backup..." >> "$LOG_FILE"

if [ ! -f "$DB_PATH" ]; then
    echo "[$(date)] ERROR: DB not found at $DB_PATH" >> "$LOG_FILE"
    exit 1
fi

# Copy to a temp file first so we never upload a half-written DB if the
# scheduler happens to be writing to it at the exact same second (SQLite
# WAL mode makes this rare, but this makes the backup atomic regardless).
#
# NOTE: /tmp is NOT writable on Termux/Android (sandboxed filesystem), so the
# temp copy is made inside the archive folder itself instead - that folder is
# guaranteed writable since it's where chain_archive.db itself lives. This
# also makes the script portable to a plain Linux/VPS box without changes.
TMP_DIR="$(dirname "$DB_PATH")/.backup_tmp"
mkdir -p "$TMP_DIR"
TMP_COPY="${TMP_DIR}/${BACKUP_NAME}"
cp "$DB_PATH" "$TMP_COPY"

rclone copy "$TMP_COPY" "$DRIVE_FOLDER/" --create-empty-src-dirs 2>> "$LOG_FILE"
rm -f "$TMP_COPY"

# Keep only the last 30 daily backups on Drive so it doesn't grow forever
rclone delete "$DRIVE_FOLDER/" --min-age 30d 2>> "$LOG_FILE" || true

echo "[$(date)] Backup complete: $BACKUP_NAME" >> "$LOG_FILE"
