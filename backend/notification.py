"""
TradePro Backend - Notification Service
Telegram, Email, Webhook notifications.
WhatsApp placeholder only.
Compatible with Python 3.11+, Termux, Linux.
"""

import json
import logging
import urllib.request
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class NotificationResult:
    success  : bool
    channel  : str
    message  : str
    error    : str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Notification Service
# ---------------------------------------------------------------------------

class NotificationService:
    """
    Send notifications via Telegram, Email, Webhook.
    WhatsApp is a placeholder for future integration.
    """

    def __init__(
        self,
        telegram_token  : str = "",
        telegram_chat_id: str = "",
        email_host      : str = "smtp.gmail.com",
        email_port      : int = 587,
        email_user      : str = "",
        email_password  : str = "",
        webhook_url     : str = "",
    ) -> None:
        self.telegram_token   = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.email_host       = email_host
        self.email_port       = email_port
        self.email_user       = email_user
        self.email_password   = email_password
        self.webhook_url      = webhook_url

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------

    def send_telegram(self, message: str) -> NotificationResult:
        """Send message via Telegram Bot API."""
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning("Telegram not configured")
            return NotificationResult(False, "telegram", message, "Not configured")

        url     = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id"   : self.telegram_chat_id,
            "text"      : message,
            "parse_mode": "HTML",
        }
        try:
            data = json.dumps(payload).encode()
            req  = urllib.request.Request(url, data=data)
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                if result.get("ok"):
                    logger.info("Telegram notification sent")
                    return NotificationResult(True, "telegram", message)
                return NotificationResult(False, "telegram", message, str(result))
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return NotificationResult(False, "telegram", message, str(e))

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------

    def send_email(
        self,
        subject  : str,
        body     : str,
        to_email : str,
    ) -> NotificationResult:
        """Send email notification via SMTP."""
        if not self.email_user or not self.email_password:
            logger.warning("Email not configured")
            return NotificationResult(False, "email", body, "Not configured")

        try:
            msg                    = MIMEMultipart()
            msg["From"]            = self.email_user
            msg["To"]              = to_email
            msg["Subject"]         = subject
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.email_host, self.email_port) as server:
                server.starttls()
                server.login(self.email_user, self.email_password)
                server.sendmail(self.email_user, to_email, msg.as_string())

            logger.info(f"Email sent to {to_email}")
            return NotificationResult(True, "email", body)
        except Exception as e:
            logger.error(f"Email error: {e}")
            return NotificationResult(False, "email", body, str(e))

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------

    def send_webhook(self, payload: dict) -> NotificationResult:
        """Send JSON payload to webhook URL."""
        if not self.webhook_url:
            logger.warning("Webhook not configured")
            return NotificationResult(False, "webhook", str(payload), "Not configured")

        try:
            data = json.dumps(payload).encode()
            req  = urllib.request.Request(self.webhook_url, data=data)
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(f"Webhook sent: status={resp.status}")
                return NotificationResult(True, "webhook", str(payload))
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return NotificationResult(False, "webhook", str(payload), str(e))

    # ------------------------------------------------------------------
    # WhatsApp (placeholder)
    # ------------------------------------------------------------------

    def send_whatsapp(self, message: str) -> NotificationResult:
        """WhatsApp placeholder — not yet implemented."""
        logger.info("WhatsApp notification: placeholder only")
        return NotificationResult(
            False, "whatsapp", message,
            "WhatsApp integration not yet implemented"
        )

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    def broadcast(self, message: str, to_email: str = "") -> list[dict]:
        """Send to all configured channels."""
        results = []
        results.append(self.send_telegram(message).to_dict())
        if to_email:
            results.append(self.send_email("TradePro Alert", message, to_email).to_dict())
        results.append(self.send_webhook({"message": message, "source": "TradePro"}).to_dict())
        return results


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

notification_service = NotificationService()
