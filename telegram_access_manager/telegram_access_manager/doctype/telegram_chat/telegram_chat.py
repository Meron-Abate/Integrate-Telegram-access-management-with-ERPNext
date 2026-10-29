import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

from telegram_access_manager import telegram as tg
from telegram_access_manager.constants import MANAGER_ROLES, METHOD_SINGLE_USE
from telegram_access_manager.services.audit import log_event
from telegram_access_manager.services.common import require_roles, run_or_enqueue, validate_telegram_chat_id
from telegram_access_manager.telegram.client import TelegramAPIError

TYPE_MAP = {"group": "Group", "supergroup": "Supergroup", "channel": "Channel"}


class TelegramChat(Document):
	def validate(self):
		self.telegram_chat_id = validate_telegram_chat_id(self.telegram_chat_id)
		if self.chat_type in ("Supergroup", "Channel") and not self.telegram_chat_id.startswith("-100"):
			frappe.msgprint(
				_("Supergroup and channel IDs normally start with -100. Please double-check the chat ID."), alert=True
			)
		if self.chat_type == "Group" and self.access_method != METHOD_SINGLE_USE:
			# Join-request links are not available in basic groups.
			self.access_method = METHOD_SINGLE_USE
		if self.is_new() and not self.flags.tam_discovered:
			self.registration_status = "Registered"
		if self.has_value_changed("telegram_chat_id") and not self.is_new():
			self.bot_status = "Unknown"
			self.last_validated_on = None
		if self.is_active and not self.flags.tam_discovered:
			self.registration_status = "Registered"
			if self.bot_status != "Administrator":
				frappe.msgprint(
					_("The bot is not confirmed as an administrator of this chat. Use 'Validate Bot Access' before granting it."),
					indicator="orange",
					alert=True,
				)

	def on_update(self):
		before = self.get_doc_before_save()
		if before and (before.is_active != self.is_active or before.telegram_chat_id != self.telegram_chat_id):
			run_or_enqueue("telegram_access_manager.services.access.recalculate_for_chat", chat=self.name)

	@frappe.whitelist(methods=["POST"])
	def reject_registration(self, reason: str | None = None):
		"""Reject a chat registered from Telegram. It stays inactive; /register is refused until reviewed again."""
		require_roles(MANAGER_ROLES, _("review Telegram chat registrations"))
		self.load_from_db()
		if self.is_active:
			frappe.throw(_("Deactivate the chat before rejecting its registration."))
		reason = (reason or "").strip()[:1000]
		self.registration_status = "Rejected"
		self.registration_notes = reason or None
		self.flags.tam_discovered = True
		self.save()
		log_event("Chat Registration Rejected", "Info", reason or "Rejected by administrator", telegram_chat=self.name)
		return self.registration_status

	@frappe.whitelist(methods=["POST"])
	def validate_bot_access(self):
		"""Check with Telegram that the chat exists and that the bot has the rights it needs."""
		require_roles(MANAGER_ROLES, _("validate Telegram chats"))
		self.load_from_db()
		client = tg.get_client()
		values = {"last_validated_on": now_datetime()}
		messages = []
		try:
			me = client.get_me()
			info = client.get_chat(self.telegram_chat_id)
			member = client.get_chat_member(self.telegram_chat_id, me["id"])
		except TelegramAPIError as e:
			values.update(bot_status="Error", invite_link_status="Unknown", last_error=e.description)
			frappe.db.set_value(self.doctype, self.name, values)
			log_event("Chat Validated", "Failed", e.description, telegram_chat=self.name)
			return {"ok": False, "error": e.description}

		actual_type = TYPE_MAP.get(info.get("type"))
		if actual_type and actual_type != self.chat_type:
			values["chat_type"] = actual_type
			if actual_type == "Group":
				values["access_method"] = METHOD_SINGLE_USE
			messages.append(_("Chat type updated to {0} (reported by Telegram).").format(actual_type))

		status = member.get("status")
		is_admin = status in ("administrator", "creator")
		can_invite = is_admin and cint(member.get("can_invite_users", status == "creator"))
		can_restrict = is_admin and cint(member.get("can_restrict_members", status == "creator"))
		values.update(
			telegram_title=(info.get("title") or "")[:140],
			bot_status="Administrator" if is_admin else ("Member" if status in ("member", "restricted") else "Not a Member"),
			bot_can_invite_users=can_invite,
			bot_can_restrict_members=can_restrict,
			invite_link_status="Available" if can_invite else "Unavailable",
			last_error=None,
		)
		if not is_admin:
			messages.append(_("The bot is not an administrator. Promote it with 'Invite users via link' (and 'Ban users' for removals)."))
		elif not can_invite:
			messages.append(_("The bot cannot invite users. Enable the 'Invite users via link' / 'Add members' admin right."))
		if is_admin and not can_restrict:
			messages.append(_("The bot cannot remove members. Enable 'Ban users' if revocation should remove people automatically."))

		frappe.db.set_value(self.doctype, self.name, values)
		log_event("Chat Validated", "Success" if is_admin and can_invite else "Failed", "; ".join(messages) or "OK", telegram_chat=self.name)
		return {"ok": bool(is_admin and can_invite), "messages": messages, "title": values["telegram_title"]}
