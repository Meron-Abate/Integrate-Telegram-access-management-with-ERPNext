"""Administrator-only integration actions (called from the Telegram Access Settings form)."""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from telegram_access_manager import telegram as tg
from telegram_access_manager.credentials import get_config_status, get_webhook_secret
from telegram_access_manager.constants import ALLOWED_UPDATES, MANAGER_ROLES, SETTINGS, WEBHOOK_METHOD_PATH
from telegram_access_manager.services.audit import log_event
from telegram_access_manager.services.common import require_roles
from telegram_access_manager.telegram.client import TelegramAPIError


def webhook_url(base_url: str | None) -> str | None:
	if not base_url:
		return None
	return base_url.rstrip("/") + WEBHOOK_METHOD_PATH


def _update_settings(**values):
	values["last_checked_on"] = now_datetime()
	for key, value in values.items():
		frappe.db.set_single_value(SETTINGS, key, value)
	frappe.clear_document_cache(SETTINGS, SETTINGS)


def _api_error(e: TelegramAPIError):
	_update_settings(webhook_status=f"Error: {e.description}")
	frappe.throw(_("Telegram API error ({0}): {1}").format(e.error_code or "network", e.description))


@frappe.whitelist(methods=["POST"])
def test_connection():
	require_roles(MANAGER_ROLES, _("test the Telegram connection"))
	try:
		me = tg.get_client().get_me()
	except TelegramAPIError as e:
		_api_error(e)
	_update_settings(bot_username=me.get("username"), bot_id=str(me.get("id")))
	return {"username": me.get("username"), "id": me.get("id"), "can_join_groups": me.get("can_join_groups")}


@frappe.whitelist(methods=["POST"])
def set_webhook(drop_pending_updates=0):
	require_roles(MANAGER_ROLES, _("configure the Telegram webhook"))
	settings = frappe.get_single(SETTINGS)
	url = webhook_url(settings.public_base_url)
	if not url or not url.startswith("https://"):
		frappe.throw(_("Set an HTTPS Public Base URL first. Telegram only delivers webhooks over HTTPS."))
	if settings.update_mode != "Webhook":
		frappe.throw(_("Update Mode is Polling. Switch it to Webhook and save before setting the webhook."))
	secret = get_webhook_secret()
	try:
		tg.get_client().set_webhook(url, secret, ALLOWED_UPDATES, drop_pending_updates=bool(cint(drop_pending_updates)))
	except TelegramAPIError as e:
		_api_error(e)
	_update_settings(webhook_status=f"Webhook set to {url}")
	log_event("Integration Configured", "Success", f"Webhook set to {url}")
	return get_webhook_info()


@frappe.whitelist(methods=["POST"])
def delete_webhook():
	require_roles(MANAGER_ROLES, _("configure the Telegram webhook"))
	try:
		tg.get_client().delete_webhook()
	except TelegramAPIError as e:
		_api_error(e)
	_update_settings(webhook_status="Webhook deleted")
	log_event("Integration Configured", "Success", "Webhook deleted")
	return {"ok": True}


@frappe.whitelist()
def get_webhook_info():
	require_roles(MANAGER_ROLES, _("view the Telegram webhook status"))
	try:
		info = tg.get_client().get_webhook_info()
	except TelegramAPIError as e:
		_api_error(e)
	safe = {
		"url": info.get("url") or "",
		"pending_update_count": info.get("pending_update_count", 0),
		"last_error_message": info.get("last_error_message"),
		"last_error_date": info.get("last_error_date"),
		"allowed_updates": info.get("allowed_updates"),
		"ip_address": info.get("ip_address"),
	}
	status = f"URL: {safe['url'] or '(none)'}; pending: {safe['pending_update_count']}"
	if safe["last_error_message"]:
		status += f"; last error: {safe['last_error_message']}"
	_update_settings(webhook_status=status)
	return safe


def get_status_for_form(settings) -> dict:
	status = get_config_status()
	status["webhook_url"] = webhook_url(settings.public_base_url)
	return status
