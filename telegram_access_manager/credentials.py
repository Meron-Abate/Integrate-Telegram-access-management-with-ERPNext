"""Server-side secret configuration.

Secrets are never stored in source code, fixtures or browser-visible fields. They are resolved, in order, from:

1. Environment variables ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_WEBHOOK_SECRET`` (deployment override), then
2. Telegram Access Settings → Bot Credentials: Frappe *Password* fields, encrypted with the site's
   encryption key; the browser only ever receives ``*****``, then
3. ``site_config.json`` keys ``telegram_bot_token`` / ``telegram_webhook_secret``
   (set with ``bench --site <site> telegram-set-secrets``).
"""

import os
import re

import frappe
from frappe import _

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TOKEN_CONF = "telegram_bot_token"
SECRET_ENV = "TELEGRAM_WEBHOOK_SECRET"
SECRET_CONF = "telegram_webhook_secret"
SETTINGS_DT = "Telegram Access Settings"
TOKEN_FIELD = "bot_token"
SECRET_FIELD = "webhook_secret"

TOKEN_RE = re.compile(r"^\d{5,20}:[A-Za-z0-9_-]{30,64}$")
# Telegram allows 1-256 characters A-Z, a-z, 0-9, _ and - for secret_token. Require a reasonable minimum.
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")


class TelegramConfigError(frappe.ValidationError):
	pass


def _settings_secret(fieldname: str) -> str | None:
	from frappe.utils.password import get_decrypted_password

	try:
		return get_decrypted_password(SETTINGS_DT, SETTINGS_DT, fieldname, raise_exception=False)
	except Exception:
		return None  # e.g. during installation, before the settings exist


def _lookup(env_key: str, conf_key: str, settings_field: str) -> tuple[str | None, str | None]:
	value = (os.environ.get(env_key) or "").strip()
	if value:
		return value, "environment"
	value = (_settings_secret(settings_field) or "").strip()
	if value:
		return value, "Settings"
	value = (frappe.conf.get(conf_key) or "").strip()
	if value:
		return value, "site_config"
	return None, None


def get_bot_token() -> str:
	token, _source = _lookup(TOKEN_ENV, TOKEN_CONF, TOKEN_FIELD)
	if not token:
		raise TelegramConfigError(
			_("Telegram bot token is not configured. Enter it in Telegram Access Settings → Bot Credentials.")
		)
	if not TOKEN_RE.match(token):
		raise TelegramConfigError(_("The configured Telegram bot token has an invalid format."))
	return token


def get_webhook_secret() -> str:
	secret, _source = _lookup(SECRET_ENV, SECRET_CONF, SECRET_FIELD)
	if not secret:
		raise TelegramConfigError(
			_("Telegram webhook secret is not configured. Use 'Generate Webhook Secret' in Telegram Access Settings.")
		)
	if not SECRET_RE.match(secret):
		raise TelegramConfigError(
			_("The webhook secret must be 32-256 characters using only A-Z, a-z, 0-9, '_' and '-'.")
		)
	return secret


def get_config_status() -> dict:
	"""Non-sensitive description of the configuration, safe to show to administrators."""
	token, token_source = _lookup(TOKEN_ENV, TOKEN_CONF, TOKEN_FIELD)
	secret, secret_source = _lookup(SECRET_ENV, SECRET_CONF, SECRET_FIELD)
	return {
		"token_configured": bool(token),
		"token_valid": bool(token and TOKEN_RE.match(token)),
		"token_source": token_source,
		# The numeric bot ID before ':' is public information (it is the bot's user ID).
		"bot_id_from_token": token.split(":", 1)[0] if token and TOKEN_RE.match(token) else None,
		"secret_configured": bool(secret),
		"secret_valid": bool(secret and SECRET_RE.match(secret)),
		"secret_source": secret_source,
	}


def current_bot_id() -> str | None:
	"""Numeric ID of the configured bot (the public part of the token before ':')."""
	token, _source = _lookup(TOKEN_ENV, TOKEN_CONF, TOKEN_FIELD)
	return token.split(":", 1)[0] if token and TOKEN_RE.match(token) else None
