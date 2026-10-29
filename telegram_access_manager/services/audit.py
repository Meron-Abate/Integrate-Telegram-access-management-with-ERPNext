import frappe

from telegram_access_manager.constants import DT_AUDIT
from telegram_access_manager.telegram.client import redact


def log_event(
	event_type: str,
	result: str = "Info",
	details: str | None = None,
	employee: str | None = None,
	employee_telegram_access: str | None = None,
	access_request: str | None = None,
	telegram_chat: str | None = None,
	telegram_user_id: str | None = None,
):
	"""Append an audit entry. Details are redacted so tokens and invite links never reach the log."""
	frappe.get_doc(
		{
			"doctype": DT_AUDIT,
			"event_type": event_type,
			"result": result,
			"actor": frappe.session.user,
			"details": redact(details)[:2000] if details else None,
			"employee": employee,
			"employee_telegram_access": employee_telegram_access,
			"access_request": access_request,
			"telegram_chat": telegram_chat,
			"telegram_user_id": telegram_user_id,
		}
	).insert(ignore_permissions=True)


def log_for_access(access_doc, event_type, result="Info", details=None, telegram_chat=None, access_request=None):
	log_event(
		event_type,
		result,
		details,
		employee=access_doc.employee,
		employee_telegram_access=access_doc.name,
		telegram_chat=telegram_chat,
		telegram_user_id=access_doc.telegram_user_id,
		access_request=access_request,
	)


def log_error(title: str, message):
	"""Error Log entry without secrets."""
	frappe.log_error(title=title[:140], message=redact(message))
