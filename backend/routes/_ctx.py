"""
Shared context for route blueprints.

server.py creates the real service instances (FyersService,
MarketDataService, etc.) once at startup and calls set_ctx(...) so
every blueprint file can reach them via get_ctx() without re-creating
anything or causing circular imports.
"""

_ctx = {}


def set_ctx(**kwargs):
    _ctx.update(kwargs)


def get_ctx():
    return _ctx
