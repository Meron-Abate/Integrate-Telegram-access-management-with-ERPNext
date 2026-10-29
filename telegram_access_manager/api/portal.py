"""JSON API for the Telegram Access portal (/telegram-access, built with frappe-ui).

The portal only shows what people need for their daily work; technical fields (sync attempts,
retry times, bot rights, internal IDs, …) stay in the ERPNext desk forms. Every endpoint checks
roles here and delegates changes to the existing DocType methods, which enforce the same rules as
the desk (document permissions, workflow checks, audit logging).

Access per chat is presented as a simple on/off switch. ``_to_overrides`` translates it into the
underlying overrides: role chat on → Inherit, role chat off → Deny, extra chat on → Allow.
"""

import frappe
from frappe import _
from frappe.utils import cint

from telegram_access_manager.constants import (
	ALLOW,
	DENY,
	DT_ACCESS,
	DT_AUDIT,
	DT_CHAT,
	DT_COMMUNITY,
	DT_REQUEST,
	DT_ROLE,
	INHERIT,
	MANAGER_ROLES,
	OPEN_REQUEST_STATUSES,
	SETTINGS,
	VERIFIER_ROLES,
)
from telegram_access_manager.credentials import get_config_status
from telegram_access_manager.services import permissions
from telegram_access_manager.services.common import has_any_role, require_roles

MEMBERSHIP_LABELS = {
	"Member": "Joined",
	"Chat Admin": "Joined (admin)",
	"Invite Sent": "Invite sent",
	"Join Requested": "Join requested",
	"Not a Member": "Not joined",
	"Left": "Left",
	"Removed": "Removed",
	"Banned": "Removed (banned)",
	"Unknown": "Not checked yet",
	"Error": "Problem",
}
REVIEW_STATUSES = ("Pending Review", "Discovered")


def _portal_user():
	require_roles(VERIFIER_ROLES, _("use Telegram Access"))


def _manager():
	require_roles(MANAGER_ROLES, _("manage Telegram access"))


def _user_name(user):
	return frappe.utils.get_fullname(user) if user else None


def _parse(value, default=None):
	if value is None or value == "":
		return default
	return frappe.parse_json(value) if isinstance(value, str) else value


# --------------------------------------------------------------------------------------- boot / home
@frappe.whitelist()
def get_boot():
	_portal_user()
	status = get_config_status()
	settings = frappe.get_cached_doc(SETTINGS)
	return {
		"user": {"name": frappe.session.user, "full_name": _user_name(frappe.session.user)},
		"is_manager": has_any_role(MANAGER_ROLES),
		"counts": _counts(),
		"bot": {
			"enabled": cint(settings.enabled),
			"mode": settings.update_mode,
			"username": settings.bot_username,
			"configured": status["token_valid"],
		},
	}


def _counts():
	return {
		"requests": frappe.db.count(DT_REQUEST, {"status": ["in", OPEN_REQUEST_STATUSES]}),
		"people": frappe.db.count(DT_ACCESS, {"is_active": 1}),
		"review": frappe.db.count(DT_ACCESS, {"is_active": 1, "review_required": 1}),
		"problems": frappe.db.count(DT_ACCESS, {"is_active": 1, "sync_status": ["in", ["Failed", "Partially Synced"]]}),
		"chats_to_review": frappe.db.count(DT_CHAT, {"is_active": 0, "registration_status": ["in", REVIEW_STATUSES]}),
		"chats": frappe.db.count(DT_CHAT, {"is_active": 1}),
	}


@frappe.whitelist()
def get_home():
	_portal_user()
	events = frappe.get_all(
		DT_AUDIT,
		fields=["event_type", "result", "creation", "employee", "telegram_chat", "telegram_user_id", "actor"],
		order_by="creation desc",
		limit=12,
	)
	names = _employee_names([e.employee for e in events])
	activity = [
		{
			"event": e.event_type,
			"result": e.result,
			"when": e.creation,
			"subject": names.get(e.employee) or e.telegram_chat or (f"Telegram user {e.telegram_user_id}" if e.telegram_user_id else ""),
			"by": "Bot" if e.actor == "Guest" else _user_name(e.actor),
		}
		for e in events
	]
	return {
		"counts": _counts(),
		"requests": get_requests("pending", limit=5),
		"chats": get_chats("review")[:5],
		"activity": activity,
	}


def _employee_names(employees):
	employees = [e for e in set(employees) if e]
	if not employees:
		return {}
	return dict(frappe.get_all("Employee", filters={"name": ["in", employees]}, fields=["name", "employee_name"], as_list=True))


# -------------------------------------------------------------------------------------- chat matrix
def _active_chats():
	return frappe.get_all(
		DT_CHAT, filters={"is_active": 1}, fields=["name", "chat_type", "community"], order_by="chat_name asc"
	)


def _chat_matrix(role, rows, state=None):
	"""All active chats plus any chat already on the document, with an on/off 'access' flag."""
	resolved = permissions.compute_for_rows(role, rows)
	role_chats = set(permissions.get_role_chats(role))
	info = {c.name: c for c in _active_chats()}
	for name in resolved:
		if name not in info:
			c = frappe.db.get_value(DT_CHAT, name, ["name", "chat_type", "community"], as_dict=True)
			if c:
				info[name] = c
	matrix = []
	for name, c in info.items():
		r = resolved.get(name) or {}
		row = {
			"chat": name,
			"type": c.chat_type,
			"community": c.community,
			"in_role": name in role_chats,
			"access": r.get("effective") == ALLOW,
			"note": r.get("note") or "",
		}
		if state is not None:
			s = state.get(name)
			row["membership"] = MEMBERSHIP_LABELS.get(s.membership_status, s.membership_status) if s else "Not checked yet"
			row["problem"] = s.last_error if s and s.sync_status in ("Failed", "Action Required") else None
			row["pending"] = bool(s and s.sync_status == "Pending")
		matrix.append(row)
	matrix.sort(key=lambda r: (not r["access"], not r["in_role"], r["chat"].lower()))
	return matrix


def _to_overrides(role, access):
	"""{chat: bool} from the switches → list of (chat, override) rows."""
	access = _parse(access, {}) or {}
	role_chats = set(permissions.get_role_chats(role))
	rows = []
	for chat, on in access.items():
		if not frappe.db.exists(DT_CHAT, chat):
			frappe.throw(_("Telegram Chat {0} does not exist.").format(chat))
		on = bool(cint(on)) if not isinstance(on, bool) else on
		if chat in role_chats:
			rows.append((chat, INHERIT if on else DENY))
		elif on:
			rows.append((chat, ALLOW))
	return rows


# ----------------------------------------------------------------------------------------- requests
@frappe.whitelist()
def get_requests(tab="pending", search=None, limit=200):
	_portal_user()
	statuses = {
		"pending": list(OPEN_REQUEST_STATUSES),
		"approved": ["Approved"],
		"closed": ["Rejected", "Cancelled"],
	}.get(tab, list(OPEN_REQUEST_STATUSES))
	or_filters = None
	if search:
		like = f"%{search}%"
		or_filters = {
			"telegram_display_name": ["like", like],
			"telegram_username": ["like", like],
			"telegram_user_id": ["like", like],
			"employee_name": ["like", like],
		}
	rows = frappe.get_list(
		DT_REQUEST,
		filters={"status": ["in", statuses]},
		or_filters=or_filters,
		fields=["name", "telegram_display_name", "telegram_username", "telegram_user_id", "employee", "employee_name", "status", "request_timestamp", "access_role"],
		order_by="request_timestamp desc",
		limit=cint(limit) or 200,
	)
	return [
		{
			"name": r.name,
			"person": r.telegram_display_name or r.telegram_username or r.telegram_user_id,
			"username": r.telegram_username,
			"employee": r.employee,
			"employee_name": r.employee_name,
			"status": r.status,
			"requested_on": r.request_timestamp,
			"role": r.access_role,
		}
		for r in rows
	]


def _request_payload(doc):
	open_ = doc.status in OPEN_REQUEST_STATUSES
	return {
		"name": doc.name,
		"status": doc.status,
		"person": doc.telegram_display_name or doc.telegram_username or doc.telegram_user_id,
		"username": doc.telegram_username,
		"telegram_user_id": doc.telegram_user_id,
		"requested_on": doc.request_timestamp,
		"employee": doc.employee,
		"employee_name": doc.employee_name,
		"role": doc.access_role,
		"verified": doc.verification_status == "Verified",
		"verified_by": _user_name(doc.verified_by),
		"decided_by": _user_name(doc.decided_by),
		"decided_on": doc.decision_on,
		"notes": doc.admin_notes,
		"notification": doc.notification_status,
		"error": doc.error_details if doc.status == "Error" else None,
		"access_record": doc.employee_telegram_access,
		"editable": open_,
		"chats": _chat_matrix(doc.access_role, doc.chat_permissions),
	}


@frappe.whitelist()
def get_request(name):
	_portal_user()
	doc = frappe.get_doc(DT_REQUEST, name)
	doc.check_permission("read")
	return _request_payload(doc)


@frappe.whitelist(methods=["POST"])
def save_request(name, employee=None, role=None, access=None):
	_portal_user()
	doc = frappe.get_doc(DT_REQUEST, name)
	if doc.status not in OPEN_REQUEST_STATUSES:
		frappe.throw(_("This request is closed."))
	doc.employee = employee or None
	doc.access_role = role or None
	doc.set("chat_permissions", [])
	for chat, override in _to_overrides(doc.access_role, access):
		doc.append("chat_permissions", {"telegram_chat": chat, "override": override})
	doc.save()
	return _request_payload(doc)


@frappe.whitelist(methods=["POST"])
def approve_request(name, notes=None):
	doc = frappe.get_doc(DT_REQUEST, name)
	doc.approve(admin_notes=notes)
	return _request_payload(frappe.get_doc(DT_REQUEST, name))


@frappe.whitelist(methods=["POST"])
def verify_request(name):
	doc = frappe.get_doc(DT_REQUEST, name)
	doc.verify_identity()
	return _request_payload(frappe.get_doc(DT_REQUEST, name))


@frappe.whitelist(methods=["POST"])
def close_request(name, action, reason=None):
	doc = frappe.get_doc(DT_REQUEST, name)
	if action == "reject":
		doc.reject(reason)
	elif action == "cancel":
		doc.cancel_request(reason)
	else:
		frappe.throw(_("Unknown action"))
	return _request_payload(frappe.get_doc(DT_REQUEST, name))


@frappe.whitelist(methods=["POST"])
def resend_request_notification(name):
	doc = frappe.get_doc(DT_REQUEST, name)
	doc.retry_notification()
	return True


@frappe.whitelist()
def search_employees(txt=None):
	"""Active employees for matching (basic, non-sensitive fields only)."""
	_portal_user()
	or_filters = None
	if txt:
		like = f"%{txt}%"
		or_filters = {"name": ["like", like], "employee_name": ["like", like]}
	return frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		or_filters=or_filters,
		fields=["name", "employee_name", "department", "designation"],
		order_by="employee_name asc",
		limit=20,
	)


# ------------------------------------------------------------------------------------------- people
def _people_status(r):
	if not r.is_active:
		return "Revoked"
	if r.review_required:
		return "Review needed"
	return {
		"Synced": "Up to date",
		"Changes Pending": "Changes pending",
		"Queued": "Updating",
		"In Progress": "Updating",
		"Failed": "Problem",
		"Partially Synced": "Problem",
		"Not Synced": "Updating",
	}.get(r.sync_status, r.sync_status)


@frappe.whitelist()
def get_people(tab="active", search=None):
	_portal_user()
	filters = {"is_active": 0} if tab == "revoked" else {"is_active": 1}
	or_filters = None
	if search:
		like = f"%{search}%"
		or_filters = {"employee_name": ["like", like], "telegram_username": ["like", like], "employee": ["like", like]}
	rows = frappe.get_list(
		DT_ACCESS,
		filters=filters,
		or_filters=or_filters,
		fields=["name", "employee", "employee_name", "telegram_username", "display_name", "access_role", "is_active", "review_required", "sync_status", "last_sync_on"],
		order_by="employee_name asc",
		limit=500,
	)
	counts = {}
	if rows:
		for parent in frappe.get_all(
			"Employee Telegram Chat Permission",
			filters={"parenttype": DT_ACCESS, "parent": ["in", [r.name for r in rows]], "effective_permission": ALLOW},
			pluck="parent",
		):
			counts[parent] = counts.get(parent, 0) + 1
	if tab == "attention":  # review needed or a Telegram problem
		rows = [r for r in rows if r.review_required or r.sync_status in ("Failed", "Partially Synced")]
	return [
		{
			"name": r.name,
			"employee": r.employee,
			"employee_name": r.employee_name,
			"username": r.telegram_username,
			"role": r.access_role,
			"chats": counts.get(r.name, 0) if r.is_active else 0,
			"status": _people_status(r),
		}
		for r in rows
	]


def _person_payload(doc):
	state = {r.telegram_chat: r for r in doc.chat_permissions}
	matrix = _chat_matrix(doc.access_role, doc.chat_permissions, state)
	if not doc.is_active:
		for row in matrix:
			row["access"] = False
	return {
		"name": doc.name,
		"employee": doc.employee,
		"employee_name": doc.employee_name,
		"employee_status": doc.employee_status,
		"username": doc.telegram_username,
		"display_name": doc.display_name,
		"telegram_user_id": doc.telegram_user_id,
		"role": doc.access_role,
		"active": bool(doc.is_active),
		"status": _people_status(doc),
		"review_reason": doc.review_reason if doc.review_required else None,
		"approved_by": _user_name(doc.approved_by),
		"approved_on": doc.verified_on,
		"last_sync_on": doc.last_sync_on,
		"chats": matrix,
	}


@frappe.whitelist()
def get_person(name):
	_portal_user()
	doc = frappe.get_doc(DT_ACCESS, name)
	doc.check_permission("read")
	return _person_payload(doc)


@frappe.whitelist(methods=["POST"])
def save_person(name, role=None, access=None, apply=1):
	"""Save role/chat changes and (by default) apply them to Telegram."""
	_manager()
	doc = frappe.get_doc(DT_ACCESS, name)
	if not doc.is_active:
		frappe.throw(_("Access is revoked. Restore it first."))
	doc.access_role = role or None
	wanted = dict(_to_overrides(doc.access_role, access))
	for row in doc.chat_permissions:
		row.override = wanted.pop(row.telegram_chat, INHERIT)
	for chat, override in wanted.items():
		doc.append("chat_permissions", {"telegram_chat": chat, "override": override})
	doc.save()
	if cint(apply):
		doc.apply_to_telegram()
	return _person_payload(frappe.get_doc(DT_ACCESS, name))


@frappe.whitelist(methods=["POST"])
def person_action(name, action, reason=None, notify=1):
	doc = frappe.get_doc(DT_ACCESS, name)
	if action == "revoke":
		doc.revoke_access(reason=reason, notify=cint(notify))
	elif action == "restore":
		doc.restore_access()
	elif action == "refresh":
		doc.refresh_membership()
	elif action == "retry":
		doc.retry_failed()
	elif action == "apply":
		doc.apply_to_telegram()
	else:
		frappe.throw(_("Unknown action"))
	return _person_payload(frappe.get_doc(DT_ACCESS, name))


# -------------------------------------------------------------------------------------------- chats
def _chat_status(c):
	if c.is_active:
		return "Active"
	if c.registration_status in REVIEW_STATUSES:
		return "To review"
	if c.registration_status == "Rejected":
		return "Rejected"
	return "Inactive"


@frappe.whitelist()
def get_chats(tab="active"):
	_portal_user()
	if tab == "review":
		filters = {"is_active": 0, "registration_status": ["in", REVIEW_STATUSES]}
	elif tab == "inactive":
		filters = {"is_active": 0, "registration_status": ["not in", REVIEW_STATUSES]}
	else:
		filters = {"is_active": 1}
	rows = frappe.get_list(
		DT_CHAT,
		filters=filters,
		fields=["name", "chat_type", "community", "telegram_title", "is_active", "registration_status", "bot_status", "bot_can_invite_users", "bot_can_restrict_members", "registered_by_name", "registered_on", "last_error"],
		order_by="chat_name asc",
		limit=500,
	)
	return [_chat_payload(r) for r in rows]


def _chat_payload(c):
	bot_ok = c.bot_status == "Administrator" and cint(c.bot_can_invite_users)
	if bot_ok:
		bot = "Ready" if cint(c.bot_can_restrict_members) else "Ready (can't remove members)"
	elif c.bot_status in (None, "", "Unknown"):
		bot = "Not checked"
	else:
		bot = "Bot needs admin rights"
	return {
		"name": c.name,
		"type": c.chat_type,
		"community": c.community,
		"title": c.telegram_title,
		"status": _chat_status(c),
		"bot": bot,
		"bot_ok": bool(bot_ok),
		"registered_by": c.registered_by_name,
		"registered_on": c.registered_on,
		"problem": c.last_error,
	}


@frappe.whitelist(methods=["POST"])
def chat_action(name, action, community=None, reason=None):
	_manager()
	doc = frappe.get_doc(DT_CHAT, name)
	result = None
	if action == "check":
		result = doc.validate_bot_access()
	elif action == "activate":
		doc.is_active = 1
		if community:
			doc.community = community
		doc.save()
	elif action == "deactivate":
		doc.is_active = 0
		doc.save()
	elif action == "reject":
		doc.reject_registration(reason)
	elif action == "set_community":
		doc.community = community or None
		doc.save()
	else:
		frappe.throw(_("Unknown action"))
	row = frappe.get_doc(DT_CHAT, name)
	return {"chat": _chat_payload(row), "result": result}


# -------------------------------------------------------------------------------------------- roles
@frappe.whitelist()
def get_roles():
	_portal_user()
	roles = frappe.get_list(DT_ROLE, fields=["name", "description", "is_active"], order_by="role_name asc", limit=500)
	result = []
	for r in roles:
		result.append(
			{
				"name": r.name,
				"description": r.description,
				"active": bool(r.is_active),
				"chats": frappe.get_all(
					"Telegram Access Role Chat", filters={"parent": r.name, "parenttype": DT_ROLE}, pluck="telegram_chat", order_by="idx asc"
				),
				"people": frappe.db.count(DT_ACCESS, {"access_role": r.name, "is_active": 1}),
				"pending": frappe.db.count(DT_ACCESS, {"access_role": r.name, "sync_status": "Changes Pending"}),
			}
		)
	return {"roles": result, "chats": [c.name for c in _active_chats()]}


@frappe.whitelist(methods=["POST"])
def save_role(role_name, chats=None, description=None, active=1, name=None):
	_manager()
	chats = _parse(chats, []) or []
	doc = frappe.get_doc(DT_ROLE, name) if name else frappe.new_doc(DT_ROLE)
	if not name:
		doc.role_name = (role_name or "").strip()
	doc.description = description
	doc.is_active = cint(active)
	doc.set("chats", [{"telegram_chat": c} for c in dict.fromkeys(chats)])
	doc.save()
	return {
		"name": doc.name,
		"pending": frappe.db.count(DT_ACCESS, {"access_role": doc.name, "sync_status": "Changes Pending"}),
	}


@frappe.whitelist(methods=["POST"])
def apply_role(name):
	doc = frappe.get_doc(DT_ROLE, name)
	return doc.apply_pending_changes()


# ----------------------------------------------------------------------------------------- settings
SETTINGS_FIELDS = (
	"enabled",
	"update_mode",
	"public_base_url",
	"default_telegram_community",
	"notify_on_approval",
	"notify_on_rejection",
	"notify_on_revocation",
	"auto_revoke_on_employee_exit",
	"invite_link_expiry_hours",
	"support_contact",
)


@frappe.whitelist()
def get_settings():
	_manager()
	doc = frappe.get_single(SETTINGS)
	status = get_config_status()
	from telegram_access_manager.api.admin import webhook_url

	return {
		"values": {f: doc.get(f) for f in SETTINGS_FIELDS},
		"bot_username": doc.bot_username,
		"token": {"configured": status["token_valid"], "source": status["token_source"]},
		"secret": {"configured": status["secret_valid"], "source": status["secret_source"]},
		"webhook_url": webhook_url(doc.public_base_url),
		"webhook_status": doc.webhook_status,
		"communities": frappe.get_all(DT_COMMUNITY, filters={"is_active": 1}, pluck="name", order_by="name asc"),
	}


@frappe.whitelist(methods=["POST"])
def save_settings(values=None, bot_token=None):
	_manager()
	values = _parse(values, {}) or {}
	doc = frappe.get_single(SETTINGS)
	for field in SETTINGS_FIELDS:
		if field in values:
			doc.set(field, values[field])
	if bot_token and bot_token.strip():
		doc.bot_token = bot_token.strip()
	doc.save()
	return get_settings()


@frappe.whitelist(methods=["POST"])
def connect_webhook():
	"""Generate a webhook secret if needed, then register the webhook with Telegram."""
	_manager()
	from telegram_access_manager.api.admin import set_webhook

	if not get_config_status()["secret_valid"]:
		frappe.get_single(SETTINGS).generate_webhook_secret()
	return set_webhook()
