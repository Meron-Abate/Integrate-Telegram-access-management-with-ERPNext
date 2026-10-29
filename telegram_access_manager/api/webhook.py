"""Public Telegram webhook endpoint.

This is the only guest-accessible method in the app. It is designed for that:
- POST only, IP rate-limited, request body size-limited;
- every request must carry ``X-Telegram-Bot-Api-Secret-Token`` equal to the configured secret
  (constant-time comparison). Without a configured secret, all requests are refused;
- the payload is only recorded (deduplicated by update_id) and processed in a background job.
  It cannot read or change ERPNext data beyond what the bot handlers do for the sending Telegram user.
"""

import hmac
import json

import frappe
from frappe.rate_limiter import rate_limit

from telegram_access_manager.bot.ingest import ingest_update
from telegram_access_manager.credentials import TelegramConfigError, get_webhook_secret
from telegram_access_manager.services.common import get_settings

MAX_BODY_BYTES = 1024 * 1024
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _deny(status_code: int, message: str):
	frappe.local.response.http_status_code = status_code
	return {"ok": False, "error": message}


def _webhook_rate_limit() -> int:
	return 1200  # requests per IP per minute; Telegram sends from a small set of addresses


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=_webhook_rate_limit, seconds=60)
def telegram_webhook():
	try:
		expected = get_webhook_secret()
	except TelegramConfigError:
		return _deny(403, "Webhook is not configured")

	supplied = frappe.get_request_header(SECRET_HEADER) or ""
	if not hmac.compare_digest(supplied.encode(), expected.encode()):
		return _deny(403, "Forbidden")

	settings = get_settings()
	if not settings.enabled or settings.update_mode != "Webhook":
		# Acknowledge so Telegram does not keep re-delivering, but do nothing.
		return {"ok": True, "ignored": True}

	body = frappe.request.get_data(cache=True) or b""
	if len(body) > MAX_BODY_BYTES:
		return _deny(413, "Payload too large")
	try:
		update = json.loads(body)
	except ValueError:
		return _deny(400, "Invalid JSON")
	if not isinstance(update, dict):
		return _deny(400, "Invalid update")

	outcome = ingest_update(update)
	if outcome == "invalid":
		return _deny(400, "Invalid update")
	return {"ok": True}
