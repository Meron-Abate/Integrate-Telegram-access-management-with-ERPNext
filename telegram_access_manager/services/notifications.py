"""Messages sent to employees in their private chat with the bot."""

import frappe
from frappe.utils import cint, escape_html

from telegram_access_manager import telegram as tg
from telegram_access_manager.credentials import TelegramConfigError
from telegram_access_manager.constants import METHOD_JOIN_REQUEST
from telegram_access_manager.services.common import get_settings
from telegram_access_manager.telegram.client import TelegramAPIError


def send(user_id, text: str, reply_markup: dict | None = None, client=None) -> tuple[bool, str | None]:
	"""Send a private message. Returns (sent, error). Never raises for Telegram/config errors."""
	try:
		(client or tg.get_client()).send_message(int(user_id), text, reply_markup=reply_markup)
		return True, None
	except TelegramAPIError as e:
		if e.error_code == 403:
			return False, "The employee has blocked the bot or never started it."
		return False, e.description
	except TelegramConfigError as e:
		return False, str(e)


def _support() -> str:
	return escape_html(get_settings().support_contact or "")


def links_message(links: list[dict], heading: str) -> str:
	hours = cint(get_settings().invite_link_expiry_hours) or 24
	lines = [f"<b>{escape_html(heading)}</b>", ""]
	join_request = False
	for link in links:
		label = "Request to join" if link["method"] == METHOD_JOIN_REQUEST else "Join"
		join_request = join_request or link["method"] == METHOD_JOIN_REQUEST
		lines.append(f'• <b>{escape_html(link["chat"])}</b>: <a href="{escape_html(link["url"])}">{label}</a>')
	lines.append("")
	if join_request:
		lines.append(
			"For “Request to join” links, Telegram sends a join request that is approved automatically "
			"for your account because your access is approved in ERPNext."
		)
	lines.append(
		f"These links are personal and expire in {hours} hours. Do not forward them: "
		"requests from other Telegram accounts are declined."
	)
	lines.append("If a link has expired, send /access to receive new links.")
	return "\n".join(lines)


def removal_message(chats: list[str]) -> str:
	names = "\n".join(f"• {escape_html(c)}" for c in chats)
	return f"<b>Your access has changed</b>\n\nYou no longer have access to:\n{names}\n\n{_support()}"


def rejection_message(status: str) -> str:
	verb = "was not approved" if status == "Rejected" else "was cancelled"
	return f"Your Telegram access request {verb}.\n\n{_support()}"


def revoked_all_message() -> str:
	return f"<b>Your access to company Telegram chats has been revoked.</b>\n\n{_support()}"
