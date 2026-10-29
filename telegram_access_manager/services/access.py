"""Employee Telegram Access: desired permissions and their recalculation.

Recalculation only changes the *desired* state (effective permission) and marks rows as Pending.
Telegram is changed exclusively by the sync service, after an administrator has approved/applied the
change (or, if enabled in settings, automatically for role changes).
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from telegram_access_manager.constants import (
	ALLOW,
	DENY,
	DT_ACCESS,
	DT_ROLE,
	INHERIT,
	ROW_PENDING,
	SOURCE_NONE,
	UNKNOWN,
)
from telegram_access_manager.services import permissions
from telegram_access_manager.services.audit import log_for_access
from telegram_access_manager.services.common import get_settings, system_save


def recalculate_rows(doc) -> bool:
	"""Recompute effective permissions on an Employee Telegram Access document (not saved).

	Membership/sync state of existing rows is preserved; rows whose effective permission changes are
	marked Pending. Returns True if any effective permission changed or a row was added.
	"""
	resolved = permissions.compute_for_rows(doc.access_role, doc.chat_permissions)
	existing = {row.telegram_chat: row for row in doc.chat_permissions if row.telegram_chat}
	changed = False
	rows = []

	for chat, res in resolved.items():
		effective = res["effective"] if doc.is_active else DENY
		note = res["note"] if doc.is_active else _("Access revoked")
		row = existing.get(chat)
		if row is None:
			row = frappe._dict(
				telegram_chat=chat, membership_status=UNKNOWN, sync_status=ROW_PENDING, sync_attempts=0
			)
			changed = True
		elif row.effective_permission != effective:
			row.sync_status = ROW_PENDING
			row.sync_attempts = 0
			row.next_retry_on = None
			row.retryable = 0
			changed = True
		row.membership_status = row.membership_status or UNKNOWN
		row.sync_status = row.sync_status or ROW_PENDING
		row.source = res["source"]
		row.override = res["override"]
		row.effective_permission = effective
		row.note = note
		rows.append(row)

	doc.set("chat_permissions", [])
	for row in rows:
		doc.append("chat_permissions", row)

	if any(r.sync_status == ROW_PENDING for r in doc.chat_permissions) and doc.sync_status not in ("Queued", "In Progress"):
		doc.sync_status = "Changes Pending"
	return changed


def find_identity_conflicts(employee: str, telegram_user_id: str) -> str | None:
	"""Return an error message if this Telegram identity / employee pair conflicts with existing records."""
	other = frappe.db.get_value(
		DT_ACCESS, {"telegram_user_id": telegram_user_id, "employee": ["!=", employee]}, ["name", "employee"], as_dict=True
	)
	if other:
		return _("Telegram user ID {0} is already linked to employee {1} ({2}). Revoke and delete that record first.").format(
			telegram_user_id, other.employee, other.name
		)
	current = frappe.db.get_value(
		DT_ACCESS, {"employee": employee}, ["name", "telegram_user_id", "is_active"], as_dict=True
	)
	if current and current.is_active and current.telegram_user_id != telegram_user_id:
		return _("Employee {0} already has an active Telegram identity ({1}). Revoke it before linking a new one.").format(
			employee, current.name
		)
	return None


def create_or_update_access_from_request(req):
	"""Create (or reactivate) the Employee Telegram Access for an approved request."""
	if conflict := find_identity_conflicts(req.employee, req.telegram_user_id):
		frappe.throw(conflict)

	name = frappe.db.get_value(DT_ACCESS, {"employee": req.employee}, "name")
	is_new = not name
	doc = frappe.new_doc(DT_ACCESS) if is_new else frappe.get_doc(DT_ACCESS, name)
	previous_identity = None if is_new else doc.telegram_user_id

	doc.update(
		{
			"employee": req.employee,
			"telegram_user_id": req.telegram_user_id,
			"telegram_username": req.telegram_username,
			"display_name": req.telegram_display_name,
			"is_active": 1,
			"status": "Active",
			"review_required": 0,
			"review_reason": None,
			"source_request": req.name,
			"verified_by": req.verified_by,
			"verified_on": req.verified_on,
			"verification_method": req.verification_method,
			"approved_by": frappe.session.user,
			"access_role": req.access_role,
			"last_interaction_on": now_datetime(),
		}
	)

	# Overrides come from the request; existing rows keep their membership state.
	requested = {r.telegram_chat: (r.override or INHERIT) for r in req.chat_permissions if r.telegram_chat}
	for row in doc.chat_permissions:
		row.override = requested.pop(row.telegram_chat, INHERIT)
	for chat, override in requested.items():
		doc.append("chat_permissions", {"telegram_chat": chat, "override": override})

	system_save(doc, insert=is_new)

	details = _("Created from request {0}").format(req.name) if is_new else _("Updated from request {0}").format(req.name)
	if previous_identity and previous_identity != req.telegram_user_id:
		details += _("; Telegram identity changed from {0}").format(previous_identity)
	log_for_access(doc, "Access Created" if is_new else "Access Updated", "Success", details, access_request=req.name)
	return doc


def revoke_access(name: str, reason: str | None = None, notify: bool = True):
	from telegram_access_manager.services.sync import enqueue_sync

	doc = frappe.get_doc(DT_ACCESS, name)
	doc.is_active = 0
	doc.status = "Revoked"
	doc.review_required = 0
	system_save(doc)
	log_for_access(doc, "Access Revoked", "Success", reason or _("Revoked by administrator"))
	enqueue_sync(doc.name, mode="manual", context="revocation", notify=notify)
	return doc


def restore_access(name: str):
	doc = frappe.get_doc(DT_ACCESS, name)
	doc.is_active = 1
	doc.status = "Active"
	system_save(doc)
	log_for_access(doc, "Access Restored", "Info", _("Desired permissions restored; apply them to Telegram to send invitations."))
	return doc


def _recalculate_many(names: list[str], reason: str):
	from telegram_access_manager.services.sync import enqueue_sync

	auto_apply = get_settings().auto_apply_role_changes
	for name in names:
		doc = frappe.get_doc(DT_ACCESS, name)
		if not recalculate_rows(doc):
			continue
		system_save(doc, rows_recalculated=True)
		log_for_access(doc, "Permissions Recalculated", "Info", reason)
		if auto_apply:
			enqueue_sync(doc.name, mode="pending", context="update")


def recalculate_for_role(role: str):
	names = frappe.get_all(DT_ACCESS, filters={"access_role": role}, pluck="name")
	_recalculate_many(names, _("Telegram Access Role {0} changed").format(role))


def recalculate_for_chat(chat: str):
	roles = frappe.get_all(
		"Telegram Access Role Chat", filters={"telegram_chat": chat, "parenttype": DT_ROLE}, pluck="parent"
	)
	names = set(frappe.get_all(DT_ACCESS, filters={"access_role": ["in", roles or [""]]}, pluck="name"))
	names |= set(
		frappe.get_all(
			"Employee Telegram Chat Permission",
			filters={"telegram_chat": chat, "parenttype": DT_ACCESS},
			pluck="parent",
		)
	)
	_recalculate_many(sorted(names), _("Telegram Chat {0} changed").format(chat))


def apply_pending_changes(role: str | None = None) -> int:
	from telegram_access_manager.services.sync import enqueue_sync

	filters = {"sync_status": "Changes Pending"}
	if role:
		filters["access_role"] = role
	names = frappe.get_all(DT_ACCESS, filters=filters, pluck="name")
	for name in names:
		enqueue_sync(name, mode="manual", context="update")
	return len(names)


def find_authorized_row(telegram_user_id, chat_name: str):
	"""ERPNext is the source of truth: is this Telegram user explicitly allowed in this chat right now?"""
	access = frappe.db.get_value(
		DT_ACCESS, {"telegram_user_id": str(telegram_user_id), "is_active": 1}, ["name", "employee"], as_dict=True
	)
	if not access:
		return None, None
	if frappe.db.get_value("Employee", access.employee, "status") != "Active":
		return access, None
	row = frappe.db.get_value(
		"Employee Telegram Chat Permission",
		{"parenttype": DT_ACCESS, "parent": access.name, "telegram_chat": chat_name, "effective_permission": ALLOW},
		["name", "idx"],
		as_dict=True,
	)
	return access, row
