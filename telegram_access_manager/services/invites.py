"""Private invitation links.

Links are created per employee and per chat, stored encrypted (Password field) and only ever sent to
the employee in a private chat with the bot. They are never written to the audit log or error log.
"""

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime
from frappe.utils.password import get_decrypted_password

from telegram_access_manager.constants import (
	DT_INVITE,
	INVITE_LINK_PREFIX,
	METHOD_JOIN_REQUEST,
	METHOD_SINGLE_USE,
)
from telegram_access_manager.services.audit import log_for_access
from telegram_access_manager.telegram.client import TelegramAPIError

# Do not reuse a link that is about to expire.
REUSE_MARGIN_MINUTES = 30


def effective_method(chat) -> str:
	# Join requests are only supported in supergroups and channels; basic groups use one-time links.
	if chat.chat_type == "Group":
		return METHOD_SINGLE_USE
	return chat.access_method or METHOD_JOIN_REQUEST


def get_link_url(link_name) -> str:
	return get_decrypted_password(DT_INVITE, link_name, "invite_link", raise_exception=False) or ""


def find_active_link(access_name: str, chat_name: str):
	cutoff = add_to_date(now_datetime(), minutes=REUSE_MARGIN_MINUTES)
	links = frappe.get_all(
		DT_INVITE,
		filters={"employee_telegram_access": access_name, "telegram_chat": chat_name, "status": "Active"},
		fields=["name", "expire_on"],
		order_by="creation desc",
	)
	for link in links:
		if not link.expire_on or get_datetime(link.expire_on) > cutoff:
			return link.name
	return None


def ensure_invite_link(client, settings, access, chat) -> tuple[str, bool]:
	"""Return (url, created). Reuses an unexpired active link for this employee and chat."""
	existing = find_active_link(access.name, chat.name)
	if existing:
		url = get_link_url(existing)
		if url:
			return url, False

	method = effective_method(chat)
	hours = cint(settings.invite_link_expiry_hours) or 24
	expire_on = add_to_date(now_datetime(), hours=hours)
	link_name = f"{INVITE_LINK_PREFIX}{access.name} {frappe.generate_hash(length=4)}"

	result = client.create_chat_invite_link(
		chat.telegram_chat_id,
		name=link_name,
		expire_date=int(get_datetime(expire_on).timestamp()),
		member_limit=1 if method == METHOD_SINGLE_USE else None,
		creates_join_request=True if method == METHOD_JOIN_REQUEST else None,
	)
	url = (result or {}).get("invite_link")
	if not url:
		raise TelegramAPIError("createChatInviteLink", None, "Telegram did not return an invite link")

	frappe.get_doc(
		{
			"doctype": DT_INVITE,
			"telegram_chat": chat.name,
			"employee_telegram_access": access.name,
			"employee": access.employee,
			"telegram_user_id": access.telegram_user_id,
			"status": "Active",
			"link_type": method,
			"link_name": link_name,
			"expire_on": expire_on,
			"invite_link": url,  # encrypted by Frappe on insert
		}
	).insert(ignore_permissions=True)
	log_for_access(access, "Invite Link Created", "Success", f"{method} link created (expires {expire_on:%Y-%m-%d %H:%M})", chat.name)
	return url, True


def revoke_links(client, access, chat) -> int:
	"""Revoke all active links of this employee for this chat. Raises TelegramAPIError on API failure."""
	revoked = 0
	for name in frappe.get_all(
		DT_INVITE,
		filters={"employee_telegram_access": access.name, "telegram_chat": chat.name, "status": "Active"},
		pluck="name",
	):
		url = get_link_url(name)
		if url:
			try:
				client.revoke_chat_invite_link(chat.telegram_chat_id, url)
			except TelegramAPIError as e:
				# An already expired/revoked link cannot be revoked again; anything else is a real failure.
				if not e.matches("invite_link_expired", "link expired", "invite link not found", "INVITE_HASH_EXPIRED"):
					raise
		frappe.db.set_value(DT_INVITE, name, {"status": "Revoked", "revoked_on": now_datetime()})
		revoked += 1
	if revoked:
		log_for_access(access, "Invite Link Revoked", "Success", f"{revoked} link(s) revoked", chat.name)
	return revoked


def mark_link_used(chat_name: str, link_name: str | None):
	if not link_name or not link_name.startswith(INVITE_LINK_PREFIX):
		return None
	name = frappe.db.get_value(DT_INVITE, {"telegram_chat": chat_name, "link_name": link_name}, "name")
	if name:
		frappe.db.set_value(DT_INVITE, name, "status", "Used")
	return name


def expire_links():
	for name in frappe.get_all(
		DT_INVITE, filters={"status": "Active", "expire_on": ["<", now_datetime()]}, pluck="name"
	):
		frappe.db.set_value(DT_INVITE, name, "status", "Expired")
