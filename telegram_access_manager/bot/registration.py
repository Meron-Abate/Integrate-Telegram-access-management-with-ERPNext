"""Registration of Telegram groups and channels from Telegram (``/register``).

Security model:
- The chat being registered is always the chat the update came from. Command arguments are never
  interpreted as a chat ID.
- Groups/supergroups: the sender must be the chat's creator or an administrator, confirmed with
  ``getChatMember``. Anonymous-admin messages (``sender_chat`` = the group) can't be tied to a
  person and are refused.
- Channels: channel posts carry no verifiable sender (``from`` is empty; ``author_signature`` is
  only a display text). The administrator first sends ``/register`` to the bot privately and
  receives a short single-use code, then posts ``/register CODE`` in the channel. The bot verifies
  with ``getChatMember`` that the code's owner administers that channel.
- The bot must be an administrator able to invite users.
- Registered chats are linked to the configured default Community and stay inactive (Pending Review)
  until an ERPNext administrator activates them. Registration never grants anyone access.
"""

import re
import secrets

import frappe
from frappe.utils import cint, escape_html, now_datetime
from frappe.utils.synchronization import filelock

from telegram_access_manager.constants import DT_CHAT, DT_COMMUNITY, SETTINGS
from telegram_access_manager.services.audit import log_event
from telegram_access_manager.services.common import CHAT_ID_RE, clean_text, display_name_of, get_settings
from telegram_access_manager.telegram.client import TelegramAPIError

COMMAND = "/register"
CODE_TTL_SECONDS = 600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
TYPE_MAP = {"group": "Group", "supergroup": "Supergroup", "channel": "Channel"}
ADMIN_STATUSES = ("creator", "administrator")

REGISTERED = "Registered"
PENDING_REVIEW = "Pending Review"
REJECTED = "Rejected"

MSG_NOT_CONFIGURED = "Telegram registration is not configured yet. Please contact the ERPNext administrator."
MSG_NOT_AUTHORIZED = "Only an authorized chat administrator can register this chat."
MSG_ANONYMOUS = (
	"You are posting as an anonymous administrator, so I can't verify who you are. "
	"Turn off “Remain anonymous” for your admin account, send /register again, then turn it back on if needed."
)
MSG_OTHER_COMMUNITY = "This chat is already assigned to another Community. An ERPNext administrator must review the assignment."
MSG_REJECTED = "Registration of this chat was rejected in ERPNext. An ERPNext administrator must review it."
MSG_INVALID_CHAT = "This chat can't be registered: Telegram didn't provide usable chat details."
MSG_TELEGRAM_ERROR = "I couldn't verify permissions with Telegram right now. Nothing was registered; please try again later."
MSG_BOT_NOT_ADMIN = (
	"I need to be an administrator of this chat with the “Invite users via link” right "
	"(and “Ban users” for removals). Nothing was registered; promote me and send /register again."
)
MSG_CHANNEL_CODE = (
	"Channels can't be registered with a plain /register because Telegram doesn't tell me who posted it.\n"
	"Send /register to me in a private chat to get a one-time code, then post “/register CODE” here."
)
MSG_CODE_INVALID = "That registration code is invalid or has expired. Send /register to me privately for a new one."


class RegistrationError(Exception):
	"""Expected refusal. ``user_message`` is safe to show in Telegram; ``reason`` goes to the audit log."""

	def __init__(self, user_message: str, reason: str | None = None):
		self.user_message = user_message
		self.reason = reason or user_message
		super().__init__(self.reason)


# ----------------------------------------------------------------------------------------- parsing
def parse_command(text: str | None, client) -> tuple[bool, list[str]]:
	"""Return (is /register addressed to this bot, arguments). Handles /register@BotUsername."""
	text = clean_text(text, 200)
	if not text.startswith("/"):
		return False, []
	parts = text.split()
	command, _, target = parts[0].partition("@")
	if command.lower() != COMMAND:
		return False, []
	if target:
		username = get_bot_identity(client).get("username")
		if not username or target.lower() != username.lower():
			return False, []  # addressed to another bot
	return True, parts[1:]


def get_bot_identity(client) -> dict:
	"""Bot user ID and username, cached in Telegram Access Settings after the first getMe call."""
	settings = get_settings()
	if settings.bot_id and settings.bot_username:
		return {"id": int(settings.bot_id), "username": settings.bot_username}
	me = client.get_me()
	frappe.db.set_single_value(SETTINGS, {"bot_id": str(me["id"]), "bot_username": me.get("username")})
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	return {"id": int(me["id"]), "username": me.get("username")}


def chat_metadata(tg_chat: dict) -> dict:
	chat_id = tg_chat.get("id")
	chat_type = TYPE_MAP.get(tg_chat.get("type"))
	if not isinstance(chat_id, int) or isinstance(chat_id, bool) or not chat_type or not CHAT_ID_RE.match(str(chat_id)):
		raise RegistrationError(MSG_INVALID_CHAT, "Update without a valid group/channel chat ID or type")
	return {
		"telegram_chat_id": str(chat_id),
		"chat_type": chat_type,
		"telegram_title": clean_text(tg_chat.get("title")) or None,
		"telegram_username": clean_text(tg_chat.get("username"), 64) or None,
		"is_forum": cint(tg_chat.get("is_forum")),
	}


# ------------------------------------------------------------------------------------- validation
def get_default_community() -> frappe._dict:
	name = get_settings().default_telegram_community
	if not name:
		raise RegistrationError(MSG_NOT_CONFIGURED, "Default Telegram Community is not set")
	community = frappe.db.get_value(DT_COMMUNITY, name, ["name", "community_name", "is_active"], as_dict=True)
	if not community:
		raise RegistrationError(MSG_NOT_CONFIGURED, f"Default Telegram Community {name} does not exist")
	if not community.is_active:
		raise RegistrationError(MSG_NOT_CONFIGURED, f"Default Telegram Community {name} is inactive")
	return community


def require_admin(client, chat_id: str, user_id: int):
	member = client.get_chat_member(chat_id, user_id)
	if (member or {}).get("status") not in ADMIN_STATUSES:
		raise RegistrationError(MSG_NOT_AUTHORIZED, f"Telegram user {user_id} is not an administrator of the chat")


def authorize_group_sender(client, message: dict) -> dict:
	chat_id = message["chat"]["id"]
	sender_chat = message.get("sender_chat")
	if sender_chat:
		if sender_chat.get("id") == chat_id:
			raise RegistrationError(MSG_ANONYMOUS, "Sent by an anonymous administrator; identity can't be verified")
		raise RegistrationError(MSG_NOT_AUTHORIZED, "Sent on behalf of another chat")
	user = message.get("from") or {}
	if not isinstance(user.get("id"), int) or user.get("is_bot"):
		raise RegistrationError(MSG_NOT_AUTHORIZED, "No verifiable sender")
	require_admin(client, str(chat_id), user["id"])
	return user


def bot_rights(client, chat_id: str) -> dict:
	"""The bot must be an admin able to create invite links / handle join requests."""
	member = client.get_chat_member(chat_id, get_bot_identity(client)["id"]) or {}
	status = member.get("status")
	if status not in ADMIN_STATUSES:
		raise RegistrationError(MSG_BOT_NOT_ADMIN, f"Bot status is '{status}'")
	can_invite = cint(member.get("can_invite_users", status == "creator"))
	if not can_invite:
		raise RegistrationError(MSG_BOT_NOT_ADMIN, "Bot lacks can_invite_users")
	can_restrict = cint(member.get("can_restrict_members", status == "creator"))
	return {
		"bot_status": "Administrator",
		"bot_can_invite_users": can_invite,
		"bot_can_restrict_members": can_restrict,
		"invite_link_status": "Available",
		"last_validated_on": now_datetime(),
		"last_error": None,
	}


# ------------------------------------------------------------------------------------ persistence
def _unique_chat_name(title: str, chat_id: str) -> str:
	name = (title or chat_id)[:120]
	if frappe.db.exists(DT_CHAT, name):
		name = f"{name} ({chat_id})"
	return name[:140]


def register_chat(meta: dict, rights: dict, community, registered_by: dict, source: str) -> tuple[str, str]:
	"""Create or update the Telegram Chat. Returns (outcome, chat name). Outcomes: created, updated, already_registered.

	Never activates a chat, never changes roles/permissions, never moves a chat between Communities.
	"""
	chat_id = meta["telegram_chat_id"]
	with filelock(f"tam_register_{chat_id.lstrip('-')}", timeout=15):
		name = frappe.db.get_value(DT_CHAT, {"telegram_chat_id": chat_id}, "name")
		if name:
			doc = frappe.get_doc(DT_CHAT, name)
			if doc.community and doc.community != community.name:
				raise RegistrationError(MSG_OTHER_COMMUNITY, f"Chat belongs to Community {doc.community}")
			if doc.registration_status == REJECTED:
				raise RegistrationError(MSG_REJECTED, "Chat registration was rejected in ERPNext")
			already = doc.is_active or doc.registration_status == REGISTERED
			doc.update({**meta, **rights})
			doc.community = doc.community or community.name
			if not already:
				doc.registration_status = PENDING_REVIEW
			doc.flags.tam_discovered = True
			doc.save(ignore_permissions=True)
			return ("already_registered" if already else "updated"), doc.name

		doc = frappe.get_doc(
			{
				"doctype": DT_CHAT,
				"chat_name": _unique_chat_name(meta.get("telegram_title"), chat_id),
				**meta,
				**rights,
				"community": community.name,
				"is_active": 0,
				"registration_status": PENDING_REVIEW,
				"registration_source": source,
				"registered_on": now_datetime(),
				"registered_by_telegram_user_id": str(registered_by.get("id") or "") or None,
				"registered_by_name": display_name_of(registered_by) or None,
				"access_method": "Single-Use Invite Link" if meta["chat_type"] == "Group" else "Join Request",
			}
		)
		doc.flags.tam_discovered = True
		doc.insert(ignore_permissions=True)
		return "created", doc.name


def outcome_message(outcome: str, community, rights: dict) -> str:
	label = escape_html(community.community_name or community.name)
	if outcome == "already_registered":
		text = f"This chat is already registered under “{label}”."
	elif outcome == "updated":
		text = f"This chat is registered under “{label}” and is still awaiting administrator review."
	else:
		text = f"This chat has been registered under “{label}” and is awaiting administrator review."
	if not rights.get("bot_can_restrict_members"):
		text += "\nNote: give me the “Ban users” right so revoked members can be removed automatically."
	return text


def _register(client, meta, registered_by, source):
	community = get_default_community()
	rights = bot_rights(client, meta["telegram_chat_id"])
	outcome, name = register_chat(meta, rights, community, registered_by, source)
	log_event(
		"Chat Registered",
		"Success",
		f"{outcome} via {source} by Telegram user {registered_by.get('id')}; community {community.name}; inactive until reviewed",
		telegram_chat=name,
		telegram_user_id=str(registered_by.get("id") or "") or None,
	)
	return outcome_message(outcome, community, rights)


def _refused(meta_chat_id, user_id, reason):
	log_event(
		"Chat Registration Refused",
		"Failed",
		f"Chat {meta_chat_id}: {reason}",
		telegram_user_id=str(user_id) if user_id else None,
	)


# --------------------------------------------------------------------------------------- handlers
def handle_group_register(client, message: dict, args: list[str]) -> str:
	"""/register sent in a group or supergroup. Returns the reply text."""
	user_id = (message.get("from") or {}).get("id")
	try:
		meta = chat_metadata(message.get("chat") or {})
		if meta["chat_type"] == "Channel":
			raise RegistrationError(MSG_INVALID_CHAT, "Channel message delivered as group message")
		user = authorize_group_sender(client, message)
		text = _register(client, meta, user, "/register Command")
		if args:
			text += "\n(Arguments are ignored: /register always registers the chat it is sent in.)"
		return text
	except RegistrationError as e:
		_refused((message.get("chat") or {}).get("id"), user_id, e.reason)
		return e.user_message
	except TelegramAPIError as e:
		_refused((message.get("chat") or {}).get("id"), user_id, f"Telegram API error: {e.description}")
		return MSG_TELEGRAM_ERROR


def issue_channel_code(user: dict) -> str:
	"""Private /register: issue a single-use code that ties a channel post to a verifiable admin."""
	previous = frappe.cache.get_value(f"tam:regcode_user:{user['id']}")
	if previous:
		frappe.cache.delete_value(f"tam:regcode:{previous}")
	code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
	frappe.cache.set_value(
		f"tam:regcode:{code}", {"id": user["id"], "first_name": user.get("first_name"), "last_name": user.get("last_name")},
		expires_in_sec=CODE_TTL_SECONDS,
	)
	frappe.cache.set_value(f"tam:regcode_user:{user['id']}", code, expires_in_sec=CODE_TTL_SECONDS)
	return code


def private_register_message(code: str) -> str:
	return (
		"<b>Register a group or channel</b>\n\n"
		"• <b>Group</b>: make me an administrator (with “Invite users via link”) and send /register in the group.\n"
		f"• <b>Channel</b>: within 10 minutes, post this in the channel:\n<code>/register {code}</code>\n\n"
		"I'll check with Telegram that you are an administrator of that chat. "
		"Registered chats stay inactive until an ERPNext administrator reviews and activates them."
	)


def handle_channel_register(client, post: dict, args: list[str]) -> tuple[int | None, str]:
	"""/register CODE posted in a channel. Returns (chat to reply to, reply text).

	Replies go privately to the verified administrator where possible, not into the channel.
	"""
	channel_id = (post.get("chat") or {}).get("id")
	issuer = None
	try:
		meta = chat_metadata(post.get("chat") or {})
		if meta["chat_type"] != "Channel":
			raise RegistrationError(MSG_INVALID_CHAT, "Channel post from a non-channel chat")
		code = (args[0] if args else "").upper()
		if not CODE_RE.match(code):
			raise RegistrationError(MSG_CHANNEL_CODE, "Channel /register without a registration code")
		issuer = frappe.cache.get_value(f"tam:regcode:{code}")
		# Single use: consume before verification so a code can't be replayed.
		frappe.cache.delete_value(f"tam:regcode:{code}")
		if not issuer:
			raise RegistrationError(MSG_CODE_INVALID, "Unknown or expired registration code")
		frappe.cache.delete_value(f"tam:regcode_user:{issuer['id']}")
		require_admin(client, meta["telegram_chat_id"], int(issuer["id"]))
		return issuer["id"], _register(client, meta, issuer, "/register Command")
	except RegistrationError as e:
		_refused(channel_id, issuer and issuer.get("id"), e.reason)
		return (issuer["id"] if issuer else channel_id), e.user_message
	except TelegramAPIError as e:
		_refused(channel_id, issuer and issuer.get("id"), f"Telegram API error: {e.description}")
		return (issuer["id"] if issuer else channel_id), MSG_TELEGRAM_ERROR


def default_community_for_discovery() -> str | None:
	"""Community for chats discovered when the bot is added (None if not configured/active)."""
	try:
		return get_default_community().name
	except RegistrationError:
		return None
