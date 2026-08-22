"""
TradePro Backend - Main Server
Flask API server with Fyers integration.
Compatible with Python 3.11+, Termux, Linux.

This file only wires up the app: services, scheduler tasks, and route
blueprints. All actual route logic lives in backend/routes/*.py, each
owned by a different area of the product so multiple people can work
on different endpoints without touching this file or each other's files.

── Rate-limit pass ──────────────────────────────────────────────────────
Two changes to reduce pressure on the Fyers API and let a rate-limit
window clear instead of being prolonged:
  1. refresh_quotes/refresh_nifty run less often (was 3s/10s, now 8s/25s)
     — still fast enough for practical use, meaningfully fewer calls/hour.
  2. _archive_chains now reports overall success/failure to the scheduler
     (so backend.scheduler's new backoff logic can pause it after repeated
     failures) and adds a small delay between each expiry's API call to
     spread the archive cycle's calls out instead of firing them back to
     back.
"""

import time

from flask import Flask
from flask_cors import CORS

from backend.logger         import setup_logging, get_logger
from backend.config         import APP_ID, SECRET, REDIRECT_URL, validate, summary
from backend.middleware     import register_middleware
from backend.error_handler  import register_error_handlers
from backend.fyers_service      import FyersService
from backend.services.market_data import MarketDataService
from backend.services            import chain_archive
from backend.scheduler          import scheduler
from backend.cache              import quote_cache, chain_cache

from backend.routes import register_routes
from backend.routes._ctx import set_ctx

# ---------------------------------------------------------------------------
# Logging — must be first
# ---------------------------------------------------------------------------

setup_logging()
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

missing = validate()
if missing:
    logger.warning(f"Missing config fields: {missing} — running in MOCK mode")
else:
    logger.info(f"Config OK: {summary()}")

# ---------------------------------------------------------------------------
# App + services
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

_svc     = FyersService(app_id=APP_ID, secret=SECRET, redirect_url=REDIRECT_URL)
_market  = MarketDataService(_svc)

register_middleware(app)
register_error_handlers(app)

# Make services reachable from every route blueprint without re-init or
# circular imports.
set_ctx(svc=_svc, market=_market)

# ---------------------------------------------------------------------------
# Scheduler tasks
# ---------------------------------------------------------------------------

def _archive_chains() -> bool:
    """
    Every 5 min during market hours, save real option-chain snapshots to disk
    — for EVERY available expiry (weekly, next-weekly, monthly, ...) not just
    whichever one happens to be "nearest". Logs per-expiry and total timing
    so the actual cost on this device/network is visible in the server logs.

    Returns False if every expiry call across both symbols failed (e.g. a
    Fyers-side outage or rate limit) so the scheduler's backoff can kick in;
    returns True as soon as at least one snapshot saves successfully, since
    a partial failure isn't the same as "Fyers is unreachable right now".
    """
    cycle_start = time.time()
    saved_count = 0
    attempted_count = 0
    for sym in ("NIFTY", "BANKNIFTY"):
        try:
            expiries = _market.get_expiries(sym).get("expiries", [])
            for exp in expiries:
                expiry_date = chain_archive.parse_expiry_to_date(exp.get("expiry", ""))
                t0 = time.time()
                result = _market.get_option_chain(symbol=sym, expiry=exp.get("expiry", ""), strike_count=20)
                attempted_count += 1
                ok = chain_archive.save_snapshot(sym, expiry_date, result)
                if ok:
                    saved_count += 1
                logger.info(f"Archive {sym} exp={expiry_date}: {time.time() - t0:.2f}s saved={ok}")
                # Small gap between calls so a 14-expiry cycle doesn't fire
                # back-to-back requests — negligible against the 5-minute
                # interval, but reduces burst pressure on the Fyers API.
                time.sleep(0.3)
        except Exception as e:
            logger.warning(f"Archive snapshot failed for {sym}: {e}")
    logger.info(f"Archive cycle complete: {saved_count} snapshots saved in {time.time() - cycle_start:.2f}s total")
    if attempted_count > 0 and saved_count == 0:
        return False
    return True


scheduler.add_task("refresh_quotes",  _market.refresh_quotes,               interval=8)
scheduler.add_task("refresh_nifty",   lambda: _market.refresh_chain("NIFTY"), interval=25)
scheduler.add_task("cache_cleanup",   lambda: (quote_cache.cleanup(), chain_cache.cleanup()), interval=60)
scheduler.add_task("archive_chains",  _archive_chains,                      interval=300)
scheduler.start()

# ---------------------------------------------------------------------------
# Routes — all registered from backend/routes/
# ---------------------------------------------------------------------------

register_routes(app)

# ===========================================================================
# Main
# ===========================================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  TradePro Backend v3.0")
    print(f"  Mode   : {'LIVE' if _svc.token else 'MOCK'}")
    print(f"  Server : http://localhost:8000")
    print(f"  Health : http://localhost:8000/api/health")
    print("=" * 50)
    app.run(host="0.0.0.0", port=8000, debug=False, use_reloader=False)
