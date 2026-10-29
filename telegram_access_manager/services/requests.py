"""Creation of access requests from Telegram (idempotent)."""

import frappe
from frappe.utils import add_to_date, cint, now_datetime
from frappe.utils.synchronization import filelock

from telegram_access_manager.constants import DT_ACCESS, DT_REQUEST, OPEN_REQUEST_STATUSES
from telegram_access_manager.services.audit import log_event
from telegram_access_manager.services.common import (
	clean_text,
	display_name_of,
	get_settings,
	system_save,
	validate_telegram_user_id,
)


def identity_from_user(user: dict) -> dict:
	return {
		"telegram_user_id": validate_telegram_user_id(user.get("id")),
		"telegram_username": clean_text(user.get("username"), 64) or None,
		"telegram_display_name": display_name_of(user) or None,
		"telegram_language": clean_text(user.get("language_code"), 16) or None,
	}


def refresh_access_identity(access_name: str, identity: dict):
	"""Usernames and display names change; the numeric ID stays the identity key."""
	current = frappe.db.get_value(DT_ACCESS, access_name, ["telegram_username", "display_name", "employee"], as_dict=True)
	updates = {"last_interaction_on": now_datetime()}
	if (current.telegram_username or None) != identity["telegram_username"]:
		updates["telegram_username"] = identity["telegram_username"]
		log_event(
			"Identity Updated",
			"Info",
			f"Username changed from @{current.telegram_username or '-'} to @{identity['telegram_username'] or '-'}",
			employee=current.employee,
			employee_telegram_access=access_name,
			telegram_user_id=identity["telegram_user_id"],
		)
	if (current.display_name or None) != identity["telegram_display_name"]:
		updates["display_name"] = identity["telegram_display_name"]
	frappe.db.set_value(DT_ACCESS, access_name, updates, update_modified=False)


def get_open_request(telegram_user_id: str):
	return frappe.db.get_value(
		DT_REQUEST,
		{"telegram_user_id": telegram_user_id, "status": ["in", OPEN_REQUEST_STATUSES]},
		"name",
		order_by="creation desc",
	)


def submit_request_from_telegram(user: dict) -> tuple[str, str | None]:
	"""Returns (outcome, document name). Outcomes: created, existing, already_active, rate_limited.

	Repeated /start or button presses (and Telegram re-deliveries) return the existing open request.
	"""
	identity = identity_from_user(user)
	user_id = identity["telegram_user_id"]

	with filelock(f"tam_request_{user_id}", timeout=10):
		access = frappe.db.get_value(DT_ACCESS, {"telegram_user_id": user_id, "is_active": 1}, "name")
		if access:
			refresh_access_identity(access, identity)
			return "already_active", access

		if existing := get_open_request(user_id):
			doc = frappe.get_doc(DT_REQUEST, existing)
			changed = {k: v for k, v in identity.items() if k != "telegram_user_id" and doc.get(k) != v}
			if changed:
				doc.update(changed)
				system_save(doc)
			return "existing", existing

		limit = cint(get_settings().max_requests_per_user_per_day) or 3
		recent = frappe.db.count(
			DT_REQUEST,
			{"telegram_user_id": user_id, "creation": [">", add_to_date(now_datetime(), days=-1)]},
		)
		if recent >= limit:
			return "rate_limited", None

		doc = frappe.get_doc(
			{
				"doctype": DT_REQUEST,
				**identity,
				"request_source": "Telegram Bot",
				"request_timestamp": now_datetime(),
				"status": "Pending Verification",
				"verification_status": "Not Verified",
			}
		)
		system_save(doc, insert=True)
		log_event("Request Created", "Info", "Requested through the Telegram bot", access_request=doc.name, telegram_user_id=user_id)
		return "created", doc.name
