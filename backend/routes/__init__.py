"""
TradePro Backend - Route Blueprints
Each module owns a set of related API routes. Register all of them
onto the Flask app from server.py with register_routes(app, ctx).

ctx is a small object carrying the shared service instances that
routes need (market data service, fyers service, etc.) so each
blueprint file doesn't need to re-import/re-init anything.
"""

from backend.routes.health import health_bp
from backend.routes.auth import auth_bp
from backend.routes.market_data import market_data_bp
from backend.routes.trading import trading_bp
from backend.routes.backtest import backtest_bp
from backend.routes.greeks_strategy import greeks_strategy_bp
from backend.routes.scanner import scanner_bp
from backend.routes.papertrade import papertrade_bp
from backend.routes.portfolio import portfolio_bp
from backend.routes.notification import notification_bp
from backend.routes.ai_chat import ai_chat_bp
from backend.routes.misc import misc_bp


def register_routes(app):
    """
    Register every blueprint on the Flask app. Call backend.routes._ctx.set_ctx(...)
    before this (server.py already does) so blueprints can reach shared
    service instances (FyersService, MarketDataService) via get_ctx().
    """
    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(market_data_bp)
    app.register_blueprint(trading_bp)
    app.register_blueprint(backtest_bp)
    app.register_blueprint(greeks_strategy_bp)
    app.register_blueprint(scanner_bp)
    app.register_blueprint(papertrade_bp)
    app.register_blueprint(portfolio_bp)
    app.register_blueprint(notification_bp)
    app.register_blueprint(ai_chat_bp)
    app.register_blueprint(misc_bp)
