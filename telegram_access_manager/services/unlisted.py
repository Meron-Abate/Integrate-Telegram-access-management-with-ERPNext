"""Members who are in a managed chat without an explicit Allow in ERPNext (for example added by a chat
administrator directly in Telegram, bypassing the bot).

They are recorded as ``Open`` so managers see them on the portal Home page and get a notification,
and can then remove them or keep them. Nothing is removed automatically here.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from telegram_access_manager import telegram as tg
from telegram_access_manager.constants import DT_CHAT, DT_UNLISTED, ROLE_MANAGER
from telegram_access_manager.services.audit import log_event
from telegram_access_manager.services.common import clean_text, display_name_of, get_settings
from telegram_access_manager.telegram.client import TelegramAPIError

OPEN = "Open"


def record(chat, user: dict, actor: dict, access=None):
	"""Create an Open entry unless one already exists for this person in this chat."""
	user_id = str(user["id"])
	if frappe.db.exists(DT_UNLISTED, {"telegram_chat": chat.name, "telegram_user_id": user_id, "status": OPEN}):
		return None
	actor_id = (actor or {}).get("id")
	doc = frappe.get_doc(
		{
			"doctype": DT_UNLISTED,
			"telegram_chat": chat.name,
			"telegram_user_id": user_id,
			"display_name": (display_name_of(user) or "")[:140] or None,
			"telegram_username": clean_text(user.get("username"), 64) or None,
			"status": OPEN,
			"joined_on": now_datetime(),
			"self_joined": 1 if actor_id and str(actor_id) == user_id else 0,
			"added_by": (display_name_of(actor) or "")[:140] or None if actor_id and str(actor_id) != user_id else None,
			"employee_telegram_access": access.name if access else None,
		}
	).insert(ignore_permissions=True)
	_notify_managers(doc, chat)
	return doc.name


def resolve(chat_name: str, user_id: str, status: str):
	for name in frappe.get_all(DT_UNLISTED, {"telegram_chat": chat_name, "telegram_user_id": str(user_id), "status": OPEN}, pluck="name"):
		frappe.db.set_value(DT_UNLISTED, name, "status", status)


def _notify_managers(doc, chat):
	subject = _("{0} is in {1} without access in ERPNext").format(doc.display_name or doc.telegram_user_id, chat.chat_name)
	try:
		managers = frappe.get_all("Has Role", filters={"role": ROLE_MANAGER, "parenttype": "User"}, pluck="parent")
		for user in set(managers):
			if user == "Administrator" or not frappe.db.get_value("User", user, "enabled"):
				continue
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"type": "Alert",
					"subject": subject,
					"document_type": DT_UNLISTED,
					"document_name": doc.name,
				}
			).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Telegram unlisted member notification failed")


def pending() -> list[dict]:
	"""Open entries still relevant: people who have since been granted access are marked Approved."""
	from telegram_access_manager.services.access import find_authorized_row

	rows = frappe.get_all(
		DT_UNLISTED,
		filters={"status": OPEN},
		fields=["name", "telegram_chat", "telegram_user_id", "display_name", "telegram_username", "added_by", "self_joined", "joined_on"],
		order_by="creation desc",
		limit=50,
	)
	out = []
	chat_names = dict(frappe.get_all(DT_CHAT, fields=["name", "chat_name"], as_list=True))
	for r in rows:
		_access, allowed = find_authorized_row(r.telegram_user_id, r.telegram_chat)
		if allowed:
			frappe.db.set_value(DT_UNLISTED, r.name, "status", "Approved")
			continue
		r["chat"] = chat_names.get(r.telegram_chat) or r.telegram_chat
		out.append(r)
	return out


def count() -> int:
	return frappe.db.count(DT_UNLISTED, {"status": OPEN})


def remove(name: str):
	doc = frappe.get_doc(DT_UNLISTED, name)
	if doc.status != OPEN:
		frappe.throw(_("This entry is already handled."))
	chat = frappe.get_doc(DT_CHAT, doc.telegram_chat)
	user_id = int(doc.telegram_user_id)
	try:
		client = tg.get_client()
		client.ban_chat_member(chat.telegram_chat_id, user_id)
		if chat.chat_type != "Group" and not get_settings().ban_on_removal:
			client.unban_chat_member(chat.telegram_chat_id, user_id)
	except TelegramAPIError as e:
		frappe.throw(_("Telegram refused: {0}. The bot needs the \u201cBan users\u201d right and cannot remove administrators.").format(e.description))
	doc.db_set("status", "Removed")
	log_event("Member Removed", "Success", "Removed by a manager: was not authorized in ERPNext", telegram_chat=chat.name, telegram_user_id=doc.telegram_user_id)


def keep(name: str):
	doc = frappe.get_doc(DT_UNLISTED, name)
	if doc.status != OPEN:
		frappe.throw(_("This entry is already handled."))
	doc.db_set("status", "Kept")
