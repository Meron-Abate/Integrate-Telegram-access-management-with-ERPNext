"""Scheduled jobs."""

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from telegram_access_manager.constants import DT_ACCESS, DT_UPDATE
from telegram_access_manager.services import invites
from telegram_access_manager.services.employee import flag_access_for_inactive_employee
from telegram_access_manager.services.sync import retry_failed_syncs


def _enabled() -> bool:
	return bool(frappe.db.get_single_value("Telegram Access Settings", "enabled"))


def retry_failed():
	if _enabled():
		retry_failed_syncs()


def expire_invite_links():
	invites.expire_links()


def review_inactive_employees():
	"""Safety net for status changes made without the Employee hook (e.g. data import / SQL)."""
	rows = frappe.get_all(
		DT_ACCESS, filters={"is_active": 1, "review_required": 0}, fields=["name", "employee", "employee_status"]
	)
	for row in rows:
		status = frappe.db.get_value("Employee", row.employee, "status")
		if status != "Active":
			flag_access_for_inactive_employee(row.employee, status)


def purge_bot_updates():
	days = cint(frappe.db.get_single_value("Telegram Access Settings", "bot_update_retention_days")) or 30
	frappe.db.delete(DT_UPDATE, {"creation": ["<", add_to_date(now_datetime(), days=-days)]})
