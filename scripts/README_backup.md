# Daily Google Drive Backup Setup (Termux / phone)

This backs up `chain_archive.db` (NIFTY + BANKNIFTY + BTC + ETH — all
expiries, all strikes, bid/ask/OI/greeks) to **your own** Google Drive
account every night at 3 AM automatically, even while TradePro itself keeps
running.

Google's own "connectors" (used inside chat apps like this one) can't run
unattended on a schedule — they need someone actively driving them. For a
script that fires by itself at 3 AM with nobody watching, the standard,
well-supported tool is **rclone**: a free command-line program that talks to
Google Drive using a token it gets once, from you, via a normal browser
login — after that it needs no further help from anyone.

## One-time setup (5-10 minutes)

**1. Install rclone in Termux**
```bash
pkg install rclone -y
```

**2. Link it to your Google Drive**
```bash
rclone config
```
Follow the prompts:
- `n` (new remote)
- name it exactly `gdrive`
- storage type: search/select `drive` (Google Drive)
- leave client_id / client_secret blank (press Enter) — uses rclone's own
- scope: `1` (full access) or `2` (read/write to files it creates, more private)
- leave root_folder_id and service_account blank
- "Edit advanced config?" → `n`
- "Use auto config?" → `y` **if you're setting this up on a phone/PC with a
  browser** — it opens a Google login page, you approve access, done.
  If you're on a headless/remote box with no browser, choose `n` and follow
  the link+code flow it gives you instead.
- "Configure this as a Shared Drive?" → `n` (unless you specifically want that)
- confirm with `y`, then `q` to quit

**3. Test it works**
```bash
rclone lsd gdrive:
```
Should list your Drive's top-level folders. If you see an error, re-run
`rclone config` and check step 2.

**4. Make the backup script executable**
```bash
chmod +x ~/TradePro/scripts/backup_to_drive.sh
```

**5. Schedule it for 3 AM daily**

Termux doesn't run cron by default. Install and enable it:
```bash
pkg install cronie -y
sv-enable crond   # or: crond &   (if sv-enable isn't available)
crontab -e
```
Add this line (adjust the path if your TradePro folder is elsewhere):
```
0 3 * * * /data/data/com.termux/files/home/TradePro/scripts/backup_to_drive.sh
```
Save and exit. That's it — every night at 3 AM the script runs by itself.

**Important (Termux only):** Android will kill background processes,
including cron, unless Termux is exempted from battery optimization AND
`termux-wake-lock` is active, or you run Termux:Boot to restart cron after a
reboot. Recommended:
```bash
pkg install termux-api termux-boot -y
```
Then in Android Settings → Apps → Termux → Battery → set to "Unrestricted".

## Where does the backup end up?

Your Google Drive → a folder called **`TradePro-Backups`** at the top
level → files named like `chain_archive_2026-07-26_03-00.db`. Only the last
30 daily backups are kept automatically (older ones are auto-deleted so
Drive doesn't fill up); TradePro's own `data/archive/chain_archive.db` (the
live, currently-growing file) is never deleted — only the nightly copies on
Drive are pruned.

## Checking it ran

```bash
tail -20 ~/TradePro/data/archive/backup.log
```

## Restoring from a backup (if the phone is lost/reset)

```bash
rclone copy "gdrive:TradePro-Backups/chain_archive_2026-07-26_03-00.db" ~/TradePro/data/archive/chain_archive.db
```
(swap in whichever backup date you want to restore)
