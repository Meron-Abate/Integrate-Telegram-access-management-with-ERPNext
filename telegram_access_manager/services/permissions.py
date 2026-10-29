"""Effective permission calculation.

Precedence rule (documented in the README and shown in the UI):

1. An individual **Deny** always wins, even if the role allows the chat.
2. An individual **Allow** grants the chat, even if the role does not include it.
3. Otherwise (**Inherit**) the chat is allowed only if the assigned, active Telegram Access Role lists it.
4. A chat that is inactive in ERPNext is never granted.

Nothing else grants access: membership of the Telegram community, of another chat, or a matching
username has no effect on a chat's effective permission.
"""

from collections.abc import Iterable, Mapping

import frappe

from telegram_access_manager.constants import (
	ALLOW,
	DENY,
	DT_CHAT,
	DT_ROLE,
	INHERIT,
	SOURCE_INDIVIDUAL,
	SOURCE_NONE,
	SOURCE_ROLE,
)


def resolve_effective(
	role_chats: Iterable[str], overrides: Mapping[str, str], active_chats: set[str]
) -> dict[str, dict]:
	"""Pure function: returns {chat: {"source", "override", "effective", "note"}} in a stable order."""
	role_chats = list(dict.fromkeys(role_chats))
	role_set = set(role_chats)
	ordered = role_chats + [c for c in overrides if c not in role_set]

	result = {}
	for chat in ordered:
		override = overrides.get(chat) or INHERIT
		if override not in (INHERIT, ALLOW, DENY):
			override = INHERIT
		in_role = chat in role_set
		note = ""

		if override == DENY:
			effective, source = DENY, SOURCE_INDIVIDUAL
			note = "Denied individually" + (" (overrides role)" if in_role else "")
		elif override == ALLOW:
			effective, source = ALLOW, SOURCE_INDIVIDUAL
			note = "Allowed through role and individually" if in_role else "Allowed individually"
		elif in_role:
			effective, source, note = ALLOW, SOURCE_ROLE, "Allowed through role"
		else:
			effective, source, note = DENY, SOURCE_NONE, "Not granted"

		if effective == ALLOW and chat not in active_chats:
			effective, note = DENY, "Chat is inactive in ERPNext"

		result[chat] = {"source": source, "override": override, "effective": effective, "note": note}
	return result


def get_role_chats(role_name: str | None) -> list[str]:
	"""Chats of an *active* Telegram Access Role, read from the database (never from the client)."""
	if not role_name:
		return []
	if not frappe.db.get_value(DT_ROLE, role_name, "is_active"):
		return []
	rows = frappe.get_all(
		"Telegram Access Role Chat",
		filters={"parent": role_name, "parenttype": DT_ROLE},
		fields=["telegram_chat"],
		order_by="idx asc",
	)
	return [r.telegram_chat for r in rows]


def get_active_chats(chats: Iterable[str]) -> set[str]:
	chats = list(set(chats))
	if not chats:
		return set()
	return set(frappe.get_all(DT_CHAT, filters={"name": ["in", chats], "is_active": 1}, pluck="name"))


def compute_for_rows(role_name: str | None, rows) -> dict[str, dict]:
	"""Effective permissions for a document's child rows (only the 'override' column is user input)."""
	overrides = {}
	for row in rows:
		if not row.telegram_chat:
			continue
		if row.telegram_chat in overrides:
			frappe.throw(frappe._("Telegram Chat {0} is listed more than once.").format(frappe.bold(row.telegram_chat)))
		overrides[row.telegram_chat] = row.override or INHERIT
	for chat in overrides:
		if not frappe.db.exists(DT_CHAT, chat):
			frappe.throw(frappe._("Telegram Chat {0} does not exist.").format(frappe.bold(chat)))
	role_chats = get_role_chats(role_name)
	active = get_active_chats(list(role_chats) + list(overrides))
	return resolve_effective(role_chats, overrides, active)


def preview_rows(resolved: dict[str, dict]) -> list[dict]:
	"""Shape for the UI preview (chat details included)."""
	if not resolved:
		return []
	info = {
		c.name: c
		for c in frappe.get_all(
			DT_CHAT,
			filters={"name": ["in", list(resolved)]},
			fields=["name", "chat_type", "community", "is_active", "bot_status", "access_method"],
		)
	}
	out = []
	for chat, r in resolved.items():
		c = info.get(chat) or frappe._dict()
		out.append(
			{
				"telegram_chat": chat,
				"chat_type": c.get("chat_type"),
				"community": c.get("community"),
				"bot_status": c.get("bot_status"),
				"access_method": c.get("access_method"),
				**r,
			}
		)
	return out
