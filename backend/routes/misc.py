"""Miscellaneous routes: scheduler status."""

from flask import Blueprint, jsonify

from backend.scheduler import scheduler

misc_bp = Blueprint("misc", __name__)


@misc_bp.route("/api/scheduler")
def scheduler_status():
    return jsonify({"success": True, "data": scheduler.status()})
