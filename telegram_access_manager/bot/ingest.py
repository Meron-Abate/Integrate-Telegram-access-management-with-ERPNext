"""Entry point for Telegram updates (from the webhook or the development poller).

Each update is recorded once by ``update_id`` (Telegram re-delivers updates until it receives HTTP
200), so duplicate deliveries are ignored. Processing happens in a background job.
"""

import frappe
from frappe.utils import now_datetime

from telegram_access_manager.credentials import current_bot_id
from telegram_access_manager.constants import DT_UPDATE
from telegram_access_manager.services.audit import log_error
from telegram_access_manager.telegram.client import redact

UPDATE_TYPES = ("message", "edited_message", "channel_post", "callback_query", "chat_join_request", "chat_member", "my_chat_member")


def describe(update: dict) -> tuple[str, str | None, str | None]:
	for kind in UPDATE_TYPES:
		if kind in update:
			payload = update[kind] or {}
			user = payload.get("from") or {}
			chat = payload.get("chat") or (payload.get("message") or {}).get("chat") or {}
			return kind, str(user.get("id") or "") or None, str(chat.get("id") or "") or None
	return "other", None, None


def update_key(update_id: int) -> str:
	"""Update numbers are only unique per bot, so the record key includes the bot ID."""
	return f"{current_bot_id() or 0}-{update_id}"


def ingest_update(update: dict, enqueue: bool = True) -> str:
	update_id = update.get("update_id")
	if not isinstance(update_id, int) or isinstance(update_id, bool) or update_id < 0:
		return "invalid"
	name = update_key(update_id)
	if frappe.db.exists(DT_UPDATE, name):
		return "duplicate"

	kind, user_id, chat_id = describe(update)
	try:
		frappe.get_doc(
			{
				"doctype": DT_UPDATE,
				"update_id": name,
				"update_type": kind,
				"telegram_user_id": user_id,
				"telegram_chat_id": chat_id,
				"received_on": now_datetime(),
				"status": "Received",
			}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		return "duplicate"

	# Join request queries must be answered within 10 seconds: never wait for a worker queue.
	if (update.get("chat_join_request") or {}).get("query_id"):
		enqueue = False

	if enqueue:
		frappe.enqueue(
			"telegram_access_manager.bot.ingest.process_update",
			queue="short",
			job_id=f"tam-update-{name}",
			deduplicate=True,
			enqueue_after_commit=True,
			update_name=name,
			update=update,
		)
	else:
		process_update(name, update)
	return "accepted"


def process_update(update_name: str, update: dict):
	from telegram_access_manager.bot import handlers

	status = frappe.db.get_value(DT_UPDATE, update_name, "status")
	if status in ("Processed", "Ignored"):
		return  # idempotent: already handled

	try:
		handled = handlers.dispatch(update)
		frappe.db.set_value(
			DT_UPDATE, update_name, {"status": "Processed" if handled else "Ignored", "processed_on": now_datetime()}
		)
	except Exception as e:
		if frappe.flags.in_test:
			raise
		frappe.db.rollback()
		log_error("Telegram update processing failed", frappe.get_traceback())
		frappe.db.set_value(
			DT_UPDATE, update_name, {"status": "Failed", "error": redact(str(e))[:500], "processed_on": now_datetime()}
		)
	if not frappe.flags.in_test:
		frappe.db.commit()
