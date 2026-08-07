"""
TradePro Backend - Main Server
Flask API server with Fyers integration.
Compatible with Python 3.11+, Termux, Linux.

This file only wires up the app: services, scheduler tasks, and route
blueprints. All actual route logic lives in backend/routes/*.py, each
owned by a different area of the product so multiple people can work
on different endpoints without touching this file or each other's files.
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

def _archive_chains():
    """
    Every 5 min during market hours, save real option-chain snapshots to disk
    — for EVERY available expiry (weekly, next-weekly, monthly, ...) not just
    whichever one happens to be "nearest". Logs per-expiry and total timing
    so the actual cost on this device/network is visible in the server logs.
    """
    cycle_start = time.time()
    saved_count = 0
    for sym in ("NIFTY", "BANKNIFTY"):
        try:
            expiries = _market.get_expiries(sym).get("expiries", [])
            for exp in expiries:
                expiry_date = chain_archive.parse_expiry_to_date(exp.get("expiry", ""))
                t0 = time.time()
                result = _market.get_option_chain(symbol=sym, expiry=exp.get("expiry", ""), strike_count=20)
                ok = chain_archive.save_snapshot(sym, expiry_date, result)
                if ok:
                    saved_count += 1
                logger.info(f"Archive {sym} exp={expiry_date}: {time.time() - t0:.2f}s saved={ok}")
        except Exception as e:
            logger.warning(f"Archive snapshot failed for {sym}: {e}")
    logger.info(f"Archive cycle complete: {saved_count} snapshots saved in {time.time() - cycle_start:.2f}s total")


scheduler.add_task("refresh_quotes",  _market.refresh_quotes,               interval=3)
scheduler.add_task("refresh_nifty",   lambda: _market.refresh_chain("NIFTY"), interval=10)
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
