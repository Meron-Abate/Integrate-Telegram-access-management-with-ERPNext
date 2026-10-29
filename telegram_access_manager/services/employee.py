"""Employee lifecycle: flag (or optionally revoke) Telegram access when an employee leaves."""

import frappe
from frappe import _

from telegram_access_manager.constants import DT_ACCESS
from telegram_access_manager.services.audit import log_error, log_for_access
from telegram_access_manager.services.common import get_settings


def on_employee_update(doc, method=None):
	if not doc.has_value_changed("status") or doc.status == "Active":
		return
	try:
		flag_access_for_inactive_employee(doc.name, doc.status)
	except Exception:
		# Never block HR from saving an Employee because of the Telegram integration.
		log_error("Telegram access review flag failed", frappe.get_traceback())


def flag_access_for_inactive_employee(employee: str, status: str):
	name = frappe.db.get_value(DT_ACCESS, {"employee": employee, "is_active": 1}, "name")
	if not name:
		return None
	reason = _("Employee status changed to {0}").format(status)
	frappe.db.set_value(DT_ACCESS, name, {"review_required": 1, "review_reason": reason})
	access = frappe.get_doc(DT_ACCESS, name)
	log_for_access(access, "Employee Status Changed", "Info", reason)
	if get_settings().auto_revoke_on_employee_exit:
		from telegram_access_manager.services.access import revoke_access

		revoke_access(name, reason=reason, notify=True)
	return name
