"""Notification test route."""

from flask import Blueprint, jsonify, request

from backend.response import error
from backend.notification import notification_service

notification_bp = Blueprint("notification", __name__)


@notification_bp.route("/api/notification/test", methods=["POST"])
def notification_test():
    data    = request.json or {}
    channel = data.get("channel", "webhook")
    message = data.get("message", "TradePro test notification")

    if channel == "telegram":
        result = notification_service.send_telegram(message)
    elif channel == "webhook":
        result = notification_service.send_webhook({"message": message})
    elif channel == "whatsapp":
        result = notification_service.send_whatsapp(message)
    else:
        return error(f"Unknown channel: {channel}", 400)

    return jsonify(result.to_dict())
