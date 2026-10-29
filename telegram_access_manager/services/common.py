import re

import frappe
from frappe import _
from frappe.utils import get_datetime

from telegram_access_manager.constants import SETTINGS

USER_ID_RE = re.compile(r"^[1-9]\d{0,19}$")
CHAT_ID_RE = re.compile(r"^-?[1-9]\d{0,19}$")


def get_settings():
	return frappe.get_cached_doc(SETTINGS)


def has_any_role(roles) -> bool:
	return bool(set(frappe.get_roles()) & set(roles))


def require_roles(roles, action: str):
	"""Server-side authorization for privileged actions (never rely on client-side button visibility)."""
	if not has_any_role(roles):
		frappe.throw(
			_("You are not permitted to {0}. Required role: {1}.").format(action, _(" or ").join(sorted(roles))),
			frappe.PermissionError,
		)


def validate_telegram_user_id(value) -> str:
	value = str(value or "").strip()
	if not USER_ID_RE.match(value):
		frappe.throw(_("Invalid Telegram user ID {0}. It must be a positive number.").format(frappe.bold(value)))
	return value


def validate_telegram_chat_id(value) -> str:
	value = str(value or "").strip()
	if not CHAT_ID_RE.match(value):
		frappe.throw(
			_("Invalid Telegram chat ID {0}. Use the numeric ID, e.g. -1001234567890.").format(frappe.bold(value))
		)
	return value


def clean_text(value, max_length=140) -> str:
	"""Normalise text received from Telegram before storing it."""
	value = str(value or "").replace("\x00", "").strip()
	return value[:max_length]


def display_name_of(user: dict) -> str:
	return clean_text(" ".join(filter(None, [user.get("first_name"), user.get("last_name")])))


def field_changed(doc, before, fieldname) -> bool:
	"""Compare a field with its saved value, normalising datetimes sent by the client as strings."""
	old, new = before.get(fieldname) or None, doc.get(fieldname) or None
	if old is None or new is None:
		return old != new
	df = doc.meta.get_field(fieldname)
	if df and df.fieldtype in ("Datetime", "Date"):
		return get_datetime(old) != get_datetime(new)
	return str(old) != str(new)


def run_or_enqueue(method: str, **kwargs):
	"""Run slow follow-up work (recalculations) in the background after commit; inline in tests."""
	if frappe.flags.in_test:
		return frappe.get_attr(method)(**kwargs)
	frappe.enqueue(method, queue="short", enqueue_after_commit=True, **kwargs)


def system_save(doc, insert=False, rows_recalculated=False):
	"""Save a document on behalf of the integration, bypassing field guards for this save only."""
	doc.flags.tam_system_update = True
	doc.flags.tam_rows_recalculated = rows_recalculated
	try:
		if insert:
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
	finally:
		doc.flags.tam_system_update = False
		doc.flags.tam_rows_recalculated = False
	return doc
