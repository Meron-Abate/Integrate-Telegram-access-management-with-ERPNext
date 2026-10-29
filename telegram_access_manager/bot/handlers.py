"""Telegram update handlers.

The bot never makes authorization decisions on its own: every grant (join request approval) is
checked against the Employee Telegram Access records in ERPNext at the moment it happens.
"""

import frappe
from frappe.utils import cint, escape_html

from telegram_access_manager import telegram as tg
from telegram_access_manager.bot import registration
from telegram_access_manager.constants import (
	ALLOW,
	CHAT_ADMIN,
	DT_ACCESS,
	DT_CHAT,
	DT_REQUEST,
	INVITE_LINK_PREFIX,
	LEFT,
	MEMBER,
	REMOVED,
)
from telegram_access_manager.services import access as access_service
from telegram_access_manager.services import invites
from telegram_access_manager.services.audit import log_event
from telegram_access_manager.services.common import clean_text, get_settings
from telegram_access_manager.services.requests import submit_request_from_telegram
from telegram_access_manager.services.sync import enqueue_sync, map_member_status
from telegram_access_manager.telegram.client import TelegramAPIError

CB_REQUEST = "tam:request"
CB_STATUS = "tam:status"
RESEND_COOLDOWN_SECONDS = 600

MAIN_KEYBOARD = {
	"inline_keyboard": [
		[{"text": "Request access", "callback_data": CB_REQUEST}],
		[{"text": "Check my status", "callback_data": CB_STATUS}],
	]
}


def dispatch(update: dict) -> bool:
	if "message" in update:
		return handle_message(update["message"])
	if "channel_post" in update:
		return handle_channel_post(update["channel_post"])
	if "callback_query" in update:
		return handle_callback(update["callback_query"])
	if "chat_join_request" in update:
		return handle_join_request(update["chat_join_request"])
	if "chat_member" in update:
		return handle_chat_member(update["chat_member"])
	if "my_chat_member" in update:
		return handle_my_chat_member(update["my_chat_member"])
	return False


# ------------------------------------------------------------------------------------------- helpers
def _safe_reply(chat_id, text):
	"""Best-effort reply: a failed reply must not undo or misreport the action already taken."""
	try:
		_reply(chat_id, text)
	except TelegramAPIError as e:
		frappe.logger("telegram_access_manager").warning(f"Reply to chat {chat_id} failed: {e.description}")


def _reply(chat_id, text, keyboard=None):
	try:
		tg.get_client().send_message(chat_id, text, reply_markup=keyboard)
	except TelegramAPIError as e:
		if e.error_code != 403:  # 403 = user blocked the bot; nothing to do
			raise


def _throttled(user_id) -> bool:
	"""Per-user message rate limit (abuse protection for the public bot)."""
	limit = cint(get_settings().max_messages_per_minute) or 20
	key = frappe.cache.make_key(f"tam:rl:{user_id}")  # raw redis commands are not site-prefixed
	count = frappe.cache.incr(key)
	if count == 1:
		frappe.cache.expire(key, 60)
	return count > limit


def _managed_chat(chat_id):
	return frappe.db.get_value(
		DT_CHAT,
		{"telegram_chat_id": str(chat_id)},
		["name", "chat_name", "is_active", "chat_type", "telegram_chat_id"],
		as_dict=True,
	)


# ------------------------------------------------------------------------------------ private chat
def handle_message(message: dict) -> bool:
	chat = message.get("chat") or {}
	if chat.get("type") in ("group", "supergroup"):
		return handle_group_message(message)
	if chat.get("type") != "private":
		return False

	user = message.get("from") or {}
	if not user.get("id") or user.get("is_bot"):
		return False
	if _throttled(user["id"]):
		return False

	text = clean_text(message.get("text"), 200)
	command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

	if command == "/start":
		return send_welcome(chat["id"])
	if command == "/request":
		return request_access(chat["id"], user)
	if command == "/status":
		return send_status(chat["id"], user)
	if command == "/access":
		return resend_links(chat["id"], user)
	if command == registration.COMMAND:
		code = registration.issue_channel_code(user)
		_reply(chat["id"], registration.private_register_message(code))
		return True
	return send_help(chat["id"])


def handle_callback(query: dict) -> bool:
	user = query.get("from") or {}
	message = query.get("message") or {}
	chat = message.get("chat") or {}
	if not user.get("id") or chat.get("type") != "private" or _throttled(user["id"]):
		return False
	try:
		tg.get_client().answer_callback_query(query["id"])
	except TelegramAPIError:
		pass  # cosmetic only
	if query.get("data") == CB_REQUEST:
		return request_access(chat["id"], user)
	if query.get("data") == CB_STATUS:
		return send_status(chat["id"], user)
	return False


def send_welcome(chat_id) -> bool:
	settings = get_settings()
	text = (
		"<b>Company Telegram access</b>\n\n"
		f"{escape_html(settings.privacy_notice or '')}\n\n"
		"Tap <b>Request access</b> to ask HR for access. Your request is reviewed by an administrator; "
		"access is granted only to the groups and channels approved for you.\n\n"
		"Commands: /request, /status, /access, /help"
	)
	_reply(chat_id, text, MAIN_KEYBOARD)
	return True


def send_help(chat_id) -> bool:
	_reply(
		chat_id,
		"/start – privacy notice and options\n"
		"/request – request access\n"
		"/status – status of your request and access\n"
		"/access – receive new invitation links for chats you are allowed to join\n"
		"/register – (chat administrators) register a group or channel for ERPNext\n\n"
		"This bot never asks for passwords or login codes.",
	)
	return True


def request_access(chat_id, user) -> bool:
	outcome, name = submit_request_from_telegram(user)
	if outcome == "created":
		text = (
			f"Your request <b>{escape_html(name)}</b> has been received and is awaiting identity verification by HR.\n\n"
			"HR may contact you through the usual company channels to confirm it is you. "
			"You will get a message here once a decision is made."
		)
	elif outcome == "existing":
		text = f"You already have an open request (<b>{escape_html(name)}</b>). It is awaiting review – no need to request again."
	elif outcome == "already_active":
		text = "You already have approved access. Send /status to see your chats or /access to receive invitation links."
	else:
		text = "You have made too many requests today. Please try again tomorrow or contact HR."
	_reply(chat_id, text)
	return True


def send_status(chat_id, user) -> bool:
	"""Only information linked to the sender's own Telegram user ID is returned."""
	user_id = str(user["id"])
	access = frappe.db.get_value(DT_ACCESS, {"telegram_user_id": user_id, "is_active": 1}, "name")
	if access:
		rows = frappe.get_all(
			"Employee Telegram Chat Permission",
			filters={"parenttype": DT_ACCESS, "parent": access, "effective_permission": ALLOW},
			fields=["telegram_chat", "membership_status"],
			order_by="idx asc",
		)
		lines = [f"• {escape_html(r.telegram_chat)} – {escape_html(r.membership_status or 'Unknown')}" for r in rows]
		text = "<b>Your access is active.</b>\n\n" + ("\n".join(lines) if lines else "No chats are currently assigned.")
		_reply(chat_id, text)
		return True

	req = frappe.db.get_value(
		DT_REQUEST, {"telegram_user_id": user_id}, ["name", "status"], as_dict=True, order_by="creation desc"
	)
	if not req:
		_reply(chat_id, "You have no access request yet. Send /request to ask for access.")
	else:
		_reply(chat_id, f"Request <b>{escape_html(req.name)}</b>: {escape_html(req.status)}")
	return True


def resend_links(chat_id, user) -> bool:
	user_id = str(user["id"])
	access = frappe.db.get_value(DT_ACCESS, {"telegram_user_id": user_id, "is_active": 1}, "name")
	if not access:
		_reply(chat_id, "You do not have approved access yet. Send /status to check your request.")
		return True
	key = f"tam:resend:{user_id}"
	if frappe.cache.get_value(key):
		_reply(chat_id, "New links were requested recently. Please wait a few minutes and try again.")
		return True
	frappe.cache.set_value(key, 1, expires_in_sec=RESEND_COOLDOWN_SECONDS)
	_reply(chat_id, "Checking your access – you will receive links for chats you have not joined yet.")
	enqueue_sync(access, mode="resend", context="resend")
	return True


# --------------------------------------------------------------------------------- managed chats
def handle_group_message(message: dict) -> bool:
	if message.get("migrate_to_chat_id"):
		return handle_group_service_message(message)
	text = message.get("text") or ""
	if not text.startswith("/"):
		return False
	client = tg.get_client()
	is_register, args = registration.parse_command(text, client)
	if not is_register:
		return False
	sender = (message.get("from") or {}).get("id") or (message.get("sender_chat") or {}).get("id")
	if sender and _throttled(sender):
		return False
	_safe_reply(message["chat"]["id"], registration.handle_group_register(client, message, args))
	return True


def handle_channel_post(post: dict) -> bool:
	text = post.get("text") or ""
	if not text.startswith("/"):
		return False
	client = tg.get_client()
	is_register, args = registration.parse_command(text, client)
	if not is_register or _throttled(f"chat{(post.get('chat') or {}).get('id')}"):
		return False
	reply_to, reply = registration.handle_channel_register(client, post, args)
	if reply_to:
		_safe_reply(reply_to, reply)
	try:  # tidy up the command post (needs the 'Delete messages' right; optional)
		client.delete_message(post["chat"]["id"], post["message_id"])
	except (TelegramAPIError, KeyError):
		pass
	return True


def handle_group_service_message(message: dict) -> bool:
	"""A basic group upgraded to a supergroup gets a new chat ID; follow it."""
	new_id = message.get("migrate_to_chat_id")
	if not new_id:
		return False
	chat = _managed_chat((message.get("chat") or {}).get("id"))
	if not chat:
		return False
	frappe.db.set_value(
		DT_CHAT, chat.name, {"telegram_chat_id": str(new_id), "chat_type": "Supergroup", "access_method": "Join Request"}
	)
	log_event("Chat Migrated", "Info", f"Group upgraded to supergroup; chat ID updated to {new_id}", telegram_chat=chat.name)
	return True


def handle_join_request(join: dict) -> bool:
	chat = _managed_chat((join.get("chat") or {}).get("id"))
	user = join.get("from") or {}
	if not chat or not user.get("id"):
		return False  # not a chat managed by ERPNext: leave the request to human admins

	user_id = str(user["id"])
	access, row = access_service.find_authorized_row(user_id, chat.name) if chat.is_active else (None, None)
	client = tg.get_client()
	link_name = (join.get("invite_link") or {}).get("name")
	query_id = join.get("query_id")  # present when this bot is assigned to process the chat's join requests

	def answer(result):
		if query_id:
			client.answer_chat_join_request_query(query_id, result)
		elif result == "approve":
			client.approve_chat_join_request(chat.telegram_chat_id, int(user_id))
		elif result == "decline":
			client.decline_chat_join_request(chat.telegram_chat_id, int(user_id))
		# "queue" without a query: leave the request pending for human administrators

	if access and row:
		answer("approve")
		invites.mark_link_used(chat.name, link_name)
		_set_membership(access.name, chat.name, MEMBER)
		log_event(
			"Join Request Approved",
			"Success",
			"Approved: explicit Allow in ERPNext",
			employee=access.employee,
			employee_telegram_access=access.name,
			telegram_chat=chat.name,
			telegram_user_id=user_id,
		)
		return True

	if get_settings().decline_unauthorized_join_requests:
		answer("decline")
		result, details = "Success", "Declined: no explicit Allow for this chat in ERPNext"
	else:
		answer("queue")
		result, details = "Info", "Not authorized in ERPNext; left pending for chat administrators"
	log_event(
		"Join Request Declined",
		result,
		details,
		employee=access.employee if access else None,
		employee_telegram_access=access.name if access else None,
		telegram_chat=chat.name,
		telegram_user_id=user_id,
	)
	return True


def handle_chat_member(update: dict) -> bool:
	"""Track actual membership; remove people who joined through our links without authorization."""
	chat = _managed_chat((update.get("chat") or {}).get("id"))
	member = update.get("new_chat_member") or {}
	user = member.get("user") or {}
	if not chat or not user.get("id") or user.get("is_bot"):
		return False

	user_id = str(user["id"])
	status = map_member_status(member)
	access, row = access_service.find_authorized_row(user_id, chat.name)
	link_name = (update.get("invite_link") or {}).get("name") or ""

	if status == MEMBER:
		invites.mark_link_used(chat.name, link_name)

	if access:
		_set_membership(access.name, chat.name, status if status in (MEMBER, CHAT_ADMIN) else LEFT)
		log_event(
			"Member Joined" if status in (MEMBER, CHAT_ADMIN) else "Member Left",
			"Info",
			f"Telegram reported status '{member.get('status')}'",
			employee=access.employee,
			employee_telegram_access=access.name,
			telegram_chat=chat.name,
			telegram_user_id=user_id,
		)

	unauthorized_join = status == MEMBER and not row and link_name.startswith(INVITE_LINK_PREFIX)
	if unauthorized_join:
		details = "Joined through an app-managed link without an explicit Allow"
		settings = get_settings()
		if settings.remove_unauthorized_joins and chat.is_active:
			client = tg.get_client()
			client.ban_chat_member(chat.telegram_chat_id, int(user_id))
			if chat.chat_type != "Group" and not settings.ban_on_removal:
				client.unban_chat_member(chat.telegram_chat_id, int(user_id))
			details += "; removed (Telegram confirmed)"
			if access:
				_set_membership(access.name, chat.name, REMOVED)
		log_event("Unauthorized Member", "Failed", details, telegram_chat=chat.name, telegram_user_id=user_id)
	return True


def handle_my_chat_member(update: dict) -> bool:
	"""The bot's own status changed in a chat: keep Telegram Chat records accurate / help registration."""
	tg_chat = update.get("chat") or {}
	if tg_chat.get("type") not in ("group", "supergroup", "channel"):
		return False
	new = update.get("new_chat_member") or {}
	bot_status = {"administrator": "Administrator", "member": "Member", "restricted": "Member"}.get(
		new.get("status"), "Not a Member"
	)
	values = {
		"bot_status": bot_status,
		"bot_can_invite_users": cint(new.get("can_invite_users")),
		"bot_can_restrict_members": cint(new.get("can_restrict_members")),
		"telegram_title": clean_text(tg_chat.get("title")),
	}
	chat = _managed_chat(tg_chat.get("id"))
	if chat:
		frappe.db.set_value(DT_CHAT, chat.name, values)
		log_event("Chat Validated", "Info", f"Bot status changed to {bot_status}", telegram_chat=chat.name)
		return True

	if bot_status == "Not a Member" or not get_settings().auto_register_discovered_chats:
		return False
	if not registration.CHAT_ID_RE.match(str(tg_chat.get("id") or "")):
		return False
	title = clean_text(tg_chat.get("title")) or str(tg_chat["id"])
	added_by = update.get("from") or {}
	name = title if not frappe.db.exists(DT_CHAT, title) else f"{title} ({tg_chat['id']})"
	doc = frappe.get_doc(
		{
			"doctype": DT_CHAT,
			"chat_name": name[:140],
			"telegram_chat_id": str(tg_chat["id"]),
			"chat_type": {"group": "Group", "supergroup": "Supergroup", "channel": "Channel"}[tg_chat["type"]],
			"is_active": 0,
			"registration_status": "Discovered",
			"registration_source": "Bot Added",
			"registered_on": frappe.utils.now_datetime(),
			"registered_by_telegram_user_id": str(added_by.get("id") or "") or None,
			"registered_by_name": registration.display_name_of(added_by) or None,
			# Organizational grouping only; the chat stays inactive until reviewed.
			"community": registration.default_community_for_discovery(),
			"access_method": "Single-Use Invite Link" if tg_chat["type"] == "group" else "Join Request",
			**values,
		}
	)
	doc.flags.tam_discovered = True
	doc.insert(ignore_permissions=True)
	log_event("Chat Discovered", "Info", "Bot was added to this chat; review and activate it", telegram_chat=doc.name)
	return True


def _set_membership(access_name, chat_name, status):
	name = frappe.db.get_value(
		"Employee Telegram Chat Permission",
		{"parenttype": DT_ACCESS, "parent": access_name, "telegram_chat": chat_name},
		"name",
	)
	if name:
		frappe.db.set_value("Employee Telegram Chat Permission", name, "membership_status", status)
