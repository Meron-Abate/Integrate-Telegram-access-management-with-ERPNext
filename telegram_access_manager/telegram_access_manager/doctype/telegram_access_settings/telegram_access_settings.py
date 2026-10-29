import os
import secrets

import frappe
from frappe import _
from frappe.model.document import Document

from telegram_access_manager.api.admin import get_status_for_form
from telegram_access_manager.constants import MANAGER_ROLES, SETTINGS
from telegram_access_manager.credentials import SECRET_RE, TOKEN_ENV, TOKEN_RE, current_bot_id
from telegram_access_manager.services.audit import log_event
from telegram_access_manager.services.common import require_roles

POLL_OFFSET_KEY = "tam:poll_offset"


class TelegramAccessSettings(Document):
	def validate_default_community(self):
		if not self.default_telegram_community:
			return
		active = frappe.db.get_value("Telegram Community", self.default_telegram_community, "is_active")
		if active is None:
			frappe.throw(_("Telegram Community {0} does not exist.").format(frappe.bold(self.default_telegram_community)))
		if not active:
			frappe.throw(_("Telegram Community {0} is inactive. Select an active Community.").format(frappe.bold(self.default_telegram_community)))

	def onload(self):
		self.set_onload("config_status", get_status_for_form(self))

	def _new_secret(self, fieldname: str) -> str | None:
		"""The newly typed value, or None if the field still holds the ***** placeholder / is empty."""
		value = (self.get(fieldname) or "").strip()
		if not value or self.is_dummy_password(value):
			return None
		self.set(fieldname, value)
		return value

	def validate_credentials(self):
		token = self._new_secret("bot_token")
		if token is not None:
			if not TOKEN_RE.match(token):
				frappe.throw(_("That is not a valid bot token. Copy it from @BotFather; it looks like 123456789:ABC-def..."))
			self.flags.bot_token_changed = True
		secret = self._new_secret("webhook_secret")
		if secret is not None and not SECRET_RE.match(secret):
			frappe.throw(_("The webhook secret must be 32-256 characters using only A-Z, a-z, 0-9, '_' and '-'."))
		if secret is not None:
			self.flags.webhook_secret_changed = True

	def on_update(self):
		if self.flags.bot_token_changed:
			self.handle_bot_change()
		if self.flags.webhook_secret_changed:
			log_event("Integration Configured", "Info", "Webhook secret changed")
			if self.update_mode == "Webhook":
				frappe.msgprint(_("Webhook secret changed. Click Telegram → Set Webhook so Telegram uses the new secret."), alert=True)

	def handle_bot_change(self):
		"""A different bot: forget the old bot's identity and polling position, then look up the new bot."""
		from telegram_access_manager import telegram as tg
		from telegram_access_manager.telegram.client import TelegramAPIError

		effective_id = current_bot_id()
		if effective_id and effective_id != (self.bot_id or ""):
			frappe.db.set_single_value(SETTINGS, {"bot_id": None, "bot_username": None})
			# Update numbers are per bot: an old offset could make Telegram discard the new bot's updates.
			frappe.cache.delete_value(POLL_OFFSET_KEY)
		try:
			me = tg.get_client().get_me()
			frappe.db.set_single_value(SETTINGS, {"bot_id": str(me["id"]), "bot_username": me.get("username")})
			message = _("Connected to @{0}.").format(me.get("username"))
		except (TelegramAPIError, frappe.ValidationError) as e:
			message = _("Token saved, but Telegram did not confirm it: {0}").format(getattr(e, "description", str(e)))
		frappe.clear_document_cache(SETTINGS, SETTINGS)
		log_event("Integration Configured", "Info", f"Bot token changed (bot ID {effective_id})")
		if os.environ.get(TOKEN_ENV):
			message += " " + _("Note: the TELEGRAM_BOT_TOKEN environment variable on the server overrides this field.")
		frappe.msgprint(message, alert=True)

	@frappe.whitelist(methods=["POST"])
	def generate_webhook_secret(self):
		require_roles(MANAGER_ROLES, _("configure the Telegram webhook"))
		self.load_from_db()
		self.webhook_secret = secrets.token_urlsafe(48)
		self.save()
		return True

	def validate(self):
		self.validate_credentials()
		if self.public_base_url:
			self.public_base_url = self.public_base_url.strip().rstrip("/")
			if not self.public_base_url.startswith("https://"):
				frappe.throw(_("Public Base URL must start with https:// (Telegram only calls HTTPS webhooks)."))
		if self.has_value_changed("update_mode") and self.update_mode == "Polling":
			frappe.msgprint(
				_("Delete the webhook (button above) before running 'bench telegram-poll'; Telegram refuses polling while a webhook is set."),
				alert=True,
			)
		self.validate_default_community()
		for field in ("max_sync_attempts", "retry_base_minutes", "retry_max_minutes", "invite_link_expiry_hours"):
			if (self.get(field) or 0) < 1:
				self.set(field, self.meta.get_field(field).default)
		if self.retry_max_minutes < self.retry_base_minutes:
			self.retry_max_minutes = self.retry_base_minutes
