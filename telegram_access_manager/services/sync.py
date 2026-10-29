"""Apply desired permissions to Telegram and record the actual result.

Desired state = ``effective_permission`` on each Employee Telegram Chat Permission row.
Actual state  = ``membership_status``, updated only from Telegram API responses/updates.

Modes:
- ``pending``: rows marked Pending (after approval/recalculation).
- ``manual``:  Pending, Failed and Action Required rows; resets the retry counter (administrator retry).
- ``retry``:   Failed rows whose bounded backoff has elapsed (scheduler).
- ``all``:     every row, e.g. to refresh membership status.
- ``resend``:  allowed rows that are not yet members (employee asked for new links with /access).
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime
from frappe.utils.synchronization import filelock

from telegram_access_manager import telegram as tg
from telegram_access_manager.credentials import TelegramConfigError
from telegram_access_manager.constants import (
	ALLOW,
	BANNED,
	CHAT_ADMIN,
	DT_ACCESS,
	DT_CHAT,
	DT_REQUEST,
	INHERIT,
	INVITE_SENT,
	MEMBER,
	NOT_A_MEMBER,
	REMOVED,
	ROW_ACTION_REQUIRED,
	ROW_FAILED,
	ROW_PENDING,
	ROW_SYNCED,
	SOURCE_NONE,
)
from telegram_access_manager.services import invites, notifications
from telegram_access_manager.services.audit import log_error, log_for_access
from telegram_access_manager.services.common import get_settings, system_save
from telegram_access_manager.telegram.client import TelegramAPIError

MODES = ("pending", "manual", "retry", "all", "resend")


def enqueue_sync(access_name: str, mode="pending", context="update", notify=True, request=None, now=False):
	if mode not in MODES:
		frappe.throw(_("Invalid sync mode"))
	frappe.db.set_value(DT_ACCESS, access_name, "sync_status", "Queued", update_modified=False)
	kwargs = dict(access_name=access_name, mode=mode, context=context, notify=notify, request=request)
	if now or frappe.flags.in_test:
		return sync_access(**kwargs)
	frappe.enqueue(
		"telegram_access_manager.services.sync.sync_access",
		queue="short",
		job_id=f"tam-sync-{access_name}-{mode}",
		deduplicate=True,
		enqueue_after_commit=True,
		**kwargs,
	)


def backoff_minutes(attempts: int, base: int, cap: int, retry_after_seconds: int | None = None) -> int:
	base = max(base, 1)
	cap = max(cap, base)
	delay = min(base * (2 ** max(attempts - 1, 0)), cap)
	if retry_after_seconds:
		delay = max(delay, -(-int(retry_after_seconds) // 60))
	return delay


def map_member_status(member: dict) -> str:
	status = (member or {}).get("status")
	if status in ("creator", "administrator"):
		return CHAT_ADMIN
	if status == "member":
		return MEMBER
	if status == "restricted":
		return MEMBER if member.get("is_member") else NOT_A_MEMBER
	if status == "kicked":
		return BANNED
	return NOT_A_MEMBER


def get_membership(client, chat, user_id: int) -> str:
	try:
		return map_member_status(client.get_chat_member(chat.telegram_chat_id, user_id))
	except TelegramAPIError as e:
		if e.error_code == 400 and e.matches("user not found", "participant_id_invalid", "member not found"):
			return NOT_A_MEMBER
		raise


class SyncResult:
	def __init__(self):
		self.links: list[dict] = []
		self.removed: list[str] = []
		self.failures = 0
		self.action_required = 0


def _select_rows(doc, mode):
	now = now_datetime()
	rows = []
	for row in doc.chat_permissions:
		if mode == "all":
			rows.append(row)
		elif mode == "pending" and row.sync_status == ROW_PENDING:
			rows.append(row)
		elif mode == "manual" and row.sync_status in (ROW_PENDING, ROW_FAILED, ROW_ACTION_REQUIRED):
			row.sync_attempts = 0
			rows.append(row)
		elif (
			mode == "retry"
			and row.sync_status == ROW_FAILED
			and row.retryable
			and (not row.next_retry_on or get_datetime(row.next_retry_on) <= now)
		):
			rows.append(row)
		elif mode == "resend" and row.effective_permission == ALLOW and row.membership_status not in (MEMBER, CHAT_ADMIN):
			rows.append(row)
	return rows


def _row_ok(row, membership, note=None):
	row.membership_status = membership
	row.sync_status = ROW_SYNCED
	row.last_sync_on = now_datetime()
	row.last_error = None
	row.retryable = 0
	row.next_retry_on = None
	row.sync_attempts = 0
	if note:
		row.note = note


def _row_action_required(row, message, result, doc):
	row.sync_status = ROW_ACTION_REQUIRED
	row.last_sync_on = now_datetime()
	row.last_error = message
	row.retryable = 0
	row.next_retry_on = None
	result.action_required += 1
	log_for_access(doc, "Manual Action Required", "Failed", message, row.telegram_chat)


def _row_failed(row, err: TelegramAPIError, settings, result, doc, chat):
	result.failures += 1
	row.sync_attempts = cint(row.sync_attempts) + 1
	row.last_sync_on = now_datetime()
	row.last_error = f"{err.method}: {err.description}"[:500]
	max_attempts = cint(settings.max_sync_attempts) or 5
	if err.retryable and row.sync_attempts < max_attempts:
		row.sync_status = ROW_FAILED
		row.retryable = 1
		delay = backoff_minutes(
			row.sync_attempts, cint(settings.retry_base_minutes) or 2, cint(settings.retry_max_minutes) or 60, err.retry_after
		)
		row.next_retry_on = add_to_date(now_datetime(), minutes=delay)
	else:
		# Non-transient (bad chat ID, missing admin rights, ...) or retries exhausted: needs an administrator.
		row.sync_status = ROW_FAILED if err.retryable else ROW_ACTION_REQUIRED
		row.retryable = 0
		row.next_retry_on = None
		if not err.retryable:
			result.action_required += 1
	log_for_access(doc, "Sync Failed", "Failed", row.last_error, row.telegram_chat)

	if not err.retryable and chat:
		updates = {"last_error": row.last_error}
		if err.matches("chat not found"):
			updates["bot_status"] = "Error"
		elif err.matches("not enough rights", "not an administrator", "bot is not a member", "chat_admin_required"):
			updates["bot_status"] = "Not a Member" if err.matches("not a member") else "Member"
		frappe.db.set_value(DT_CHAT, chat.name, updates, update_modified=False)


def _sync_row(client, settings, doc, row, chat, result, context):
	user_id = int(doc.telegram_user_id)
	want = bool(doc.is_active) and row.effective_permission == ALLOW and bool(chat.is_active)

	if not chat.telegram_chat_id:
		return _row_action_required(row, _("Chat has no Telegram chat ID"), result, doc)

	actual = get_membership(client, chat, user_id)

	if want:
		if actual in (MEMBER, CHAT_ADMIN):
			return _row_ok(row, actual)
		if actual == BANNED:
			if chat.chat_type == "Group":
				return _row_action_required(
					row, _("User is banned in a basic group; unban manually in Telegram first"), result, doc
				)
			client.unban_chat_member(chat.telegram_chat_id, user_id)
		url, created = invites.ensure_invite_link(client, settings, doc, chat)
		if created or context in ("approval", "resend"):
			result.links.append({"chat": chat.chat_name, "url": url, "method": invites.effective_method(chat)})
		return _row_ok(row, INVITE_SENT)

	# Not (or no longer) allowed: revoke links, then remove from the chat if Telegram allows it.
	invites.revoke_links(client, doc, chat)
	if actual == CHAT_ADMIN:
		return _row_action_required(
			row, _("User is an administrator/owner of this chat; the bot cannot remove them. Remove manually."), result, doc
		)
	if actual == MEMBER:
		if not settings.remove_member_on_revoke:
			return _row_action_required(row, _("Automatic removal is disabled; remove the member manually"), result, doc)
		client.ban_chat_member(chat.telegram_chat_id, user_id)
		membership = BANNED
		if not settings.ban_on_removal and chat.chat_type != "Group":
			client.unban_chat_member(chat.telegram_chat_id, user_id)
			membership = REMOVED
		result.removed.append(chat.chat_name)
		log_for_access(doc, "Member Removed", "Success", _("Removed from chat (Telegram confirmed)"), chat.name)
		return _row_ok(row, membership)
	return _row_ok(row, actual)


def _aggregate_status(doc) -> str:
	statuses = [r.sync_status for r in doc.chat_permissions]
	if not statuses:
		return "Synced"
	bad = sum(s in (ROW_FAILED, ROW_ACTION_REQUIRED) for s in statuses)
	if any(s == ROW_PENDING for s in statuses):
		return "Changes Pending"
	if bad == len(statuses):
		return "Failed"
	if bad:
		return "Partially Synced"
	return "Synced"


def _prune_rows(doc):
	"""Drop rows that are no longer granted and are fully cleaned up in Telegram."""
	keep = [
		r
		for r in doc.chat_permissions
		if not (
			r.source == SOURCE_NONE
			and (r.override or INHERIT) == INHERIT
			and r.sync_status == ROW_SYNCED
			and r.membership_status not in (MEMBER, CHAT_ADMIN, INVITE_SENT)
		)
	]
	if len(keep) != len(doc.chat_permissions):
		doc.set("chat_permissions", keep)


def sync_access(access_name: str, mode="pending", context="update", notify=True, request=None):
	"""Background job. Idempotent: re-running it re-checks Telegram and only performs missing actions."""
	try:
		with filelock(f"tam_sync_{access_name}", timeout=60):
			return _sync_access(access_name, mode, context, notify, request)
	except Exception as e:
		log_error(f"Telegram sync failed for {access_name}", frappe.get_traceback())
		if not frappe.flags.in_test:
			frappe.db.rollback()
			frappe.db.set_value(DT_ACCESS, access_name, {"sync_status": "Failed", "last_sync_error": str(e)[:500]})
			frappe.db.commit()
		raise


def _sync_access(access_name, mode, context, notify, request):
	settings = get_settings()
	doc = frappe.get_doc(DT_ACCESS, access_name)
	doc.sync_status = "In Progress"

	try:
		client = tg.get_client()
	except TelegramConfigError as e:
		doc.sync_status = "Failed"
		doc.last_sync_error = str(e)
		_save(doc)
		log_for_access(doc, "Sync Failed", "Failed", str(e))
		if request:
			frappe.db.set_value(DT_REQUEST, request, {"notification_status": "Failed", "error_details": str(e)})
		return {"status": "Failed", "error": str(e)}

	result = SyncResult()
	chats = {}
	for row in _select_rows(doc, mode):
		chat = chats.get(row.telegram_chat) or frappe.get_doc(DT_CHAT, row.telegram_chat)
		chats[row.telegram_chat] = chat
		try:
			_sync_row(client, settings, doc, row, chat, result, context)
		except TelegramAPIError as e:
			_row_failed(row, e, settings, result, doc, chat)

	for chat in chats.values():
		frappe.db.set_value(DT_CHAT, chat.name, "last_sync_on", now_datetime(), update_modified=False)

	_prune_rows(doc)
	doc.sync_status = _aggregate_status(doc)
	doc.last_sync_on = now_datetime()
	doc.last_sync_error = None if not (result.failures or result.action_required) else _(
		"{0} chat(s) failed, {1} need manual action. See the chat permission rows."
	).format(result.failures, result.action_required)
	_save(doc)

	log_for_access(
		doc,
		"Sync Completed" if doc.sync_status == "Synced" else "Sync Failed",
		"Success" if doc.sync_status == "Synced" else "Failed",
		_("Mode {0}: {1} link(s) issued, {2} removal(s), {3} failure(s), {4} manual action(s)").format(
			mode, len(result.links), len(result.removed), result.failures, result.action_required
		),
		access_request=request,
	)

	if notify:
		_notify(client, settings, doc, result, context, request)
	return {"status": doc.sync_status, "links": len(result.links), "removed": len(result.removed)}


def _save(doc):
	system_save(doc, rows_recalculated=True)  # keep the desired state exactly as the sync saw it
	if not frappe.flags.in_test:
		frappe.db.commit()


def _notify(client, settings, doc, result, context, request):
	messages = []
	if result.links and (context != "approval" or settings.notify_on_approval):
		heading = _("Access approved") if context == "approval" else _("Your Telegram invitations")
		messages.append(notifications.links_message(result.links, heading))
	if context == "revocation" and not doc.is_active and settings.notify_on_revocation:
		messages.append(notifications.revoked_all_message())
	elif result.removed and settings.notify_on_revocation:
		messages.append(notifications.removal_message(result.removed))

	if not messages:
		if request:
			frappe.db.set_value(DT_REQUEST, request, "notification_status", "Not Required")
		return

	sent, error = True, None
	for text in messages:
		ok, err = notifications.send(doc.telegram_user_id, text, client=client)
		sent, error = sent and ok, error or err

	log_for_access(
		doc, "Notification Sent" if sent else "Notification Failed", "Success" if sent else "Failed", error, access_request=request
	)
	if request:
		frappe.db.set_value(
			DT_REQUEST, request, {"notification_status": "Sent" if sent else "Failed", "error_details": error}
		)


def retry_failed_syncs():
	"""Scheduler: retry rows whose bounded backoff has elapsed."""
	parents = frappe.get_all(
		"Employee Telegram Chat Permission",
		filters={
			"parenttype": DT_ACCESS,
			"sync_status": ROW_FAILED,
			"retryable": 1,
			"next_retry_on": ["<=", now_datetime()],
		},
		pluck="parent",
		distinct=True,
	)
	for name in parents:
		enqueue_sync(name, mode="retry", context="retry")
