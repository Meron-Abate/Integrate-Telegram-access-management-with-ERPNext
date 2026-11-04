"""Bot message templates.

Each message can be overridden in Telegram Access Settings (field ``msg_<key>``). An empty field
falls back to the built-in default below, so nothing breaks if a template is cleared. Templates are
Telegram HTML; ``{placeholders}`` are replaced with HTML-escaped values.
"""

import frappe
from frappe.utils import escape_html

from telegram_access_manager.services.common import get_settings

COMMON = ("support",)

# key -> (label, placeholders, default)
MESSAGES = {
	"welcome": (
		"Welcome (/start)",
		("privacy_notice",),
		"<b>Company Telegram access</b>\n\n"
		"{privacy_notice}\n\n"
		"Tap <b>Request access</b> to ask HR for access. Your request is reviewed by an administrator; "
		"access is granted only to the groups and channels approved for you.\n\n"
		"Commands: /request, /status, /access, /help",
	),
	"help": (
		"Help (/help and unknown commands)",
		(),
		"/start – privacy notice and options\n"
		"/request – request access\n"
		"/status – status of your request and access\n"
		"/access – receive new invitation links for chats you are allowed to join\n"
		"/register – (chat administrators) register a group or channel for ERPNext\n\n"
		"This bot never asks for passwords or login codes.",
	),
	"request_received": (
		"Access request received",
		("request",),
		"Your request <b>{request}</b> has been received and is awaiting identity verification by HR.\n\n"
		"HR may contact you through the usual company channels to confirm it is you. "
		"You will get a message here once a decision is made.",
	),
	"request_existing": (
		"Access request already open",
		("request",),
		"You already have an open request (<b>{request}</b>). It is awaiting review – no need to request again.",
	),
	"request_already_active": (
		"Access request when already approved",
		(),
		"You already have approved access. Send /status to see your chats or /access to receive invitation links.",
	),
	"request_rate_limited": (
		"Too many access requests",
		(),
		"You have made too many requests today. Please try again tomorrow or contact HR.",
	),
	"status_active": (
		"Status: access is active",
		("chats",),
		"<b>Your access is active.</b>\n\n{chats}",
	),
	"status_no_request": (
		"Status: no request yet",
		(),
		"You have no access request yet. Send /request to ask for access.",
	),
	"status_request": (
		"Status: request in progress",
		("request", "status"),
		"Request <b>{request}</b>: {status}",
	),
	"access_not_approved": (
		"/access without approved access",
		(),
		"You do not have approved access yet. Send /status to check your request.",
	),
	"access_cooldown": (
		"/access requested too soon",
		(),
		"New links were requested recently. Please wait a few minutes and try again.",
	),
	"access_checking": (
		"/access accepted",
		(),
		"Checking your access – you will receive links for chats you have not joined yet.",
	),
	"links_approved_heading": (
		"Invitation links: heading after approval",
		(),
		"Access approved",
	),
	"links_heading": (
		"Invitation links: heading for resent links",
		(),
		"Your Telegram invitations",
	),
	"links_footer": (
		"Invitation links: closing note",
		("hours",),
		"These links are personal and expire in {hours} hours. Do not forward them: "
		"requests from other Telegram accounts are declined.\n"
		"If a link has expired, send /access to receive new links.",
	),
	"request_rejected": (
		"Request rejected",
		("support",),
		"Your Telegram access request was not approved.\n\n{support}",
	),
	"request_cancelled": (
		"Request cancelled",
		("support",),
		"Your Telegram access request was cancelled.\n\n{support}",
	),
	"access_changed": (
		"Access removed from some chats",
		("chats", "support"),
		"<b>Your access has changed</b>\n\nYou no longer have access to:\n{chats}\n\n{support}",
	),
	"access_revoked": (
		"All access revoked",
		("support",),
		"<b>Your access to company Telegram chats has been revoked.</b>\n\n{support}",
	),
}


class Raw(str):
	"""A placeholder value that is already HTML and must not be escaped again."""


class _Values(dict):
	def __missing__(self, key):
		return "{" + key + "}"  # unknown placeholder: leave visible instead of failing


def default(key: str) -> str:
	return MESSAGES[key][2]


def field(key: str) -> str:
	return f"msg_{key}"


def render(key: str, **values) -> str:
	"""The configured template for ``key`` with ``values`` filled in (built-in default if unset or malformed)."""
	if "support" in MESSAGES[key][1] and "support" not in values:
		values["support"] = get_settings().support_contact or ""
	safe = _Values({k: v if isinstance(v, Raw) else escape_html(str(v if v is not None else "")) for k, v in values.items()})
	template = (get_settings().get(field(key)) or "").strip()
	if template:
		try:
			return template.format_map(safe)
		except (ValueError, IndexError, KeyError, AttributeError):
			frappe.logger("telegram_access_manager").warning(f"Message template '{key}' is malformed; using the default")
	return default(key).format_map(safe)
