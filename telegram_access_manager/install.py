import frappe

from telegram_access_manager.constants import DT_ACTIVITY, ROLE_MANAGER, ROLE_VERIFIER

ROLES = {
	ROLE_MANAGER: "Configures the Telegram integration, chats and access roles; approves, rejects and revokes access.",
	ROLE_VERIFIER: "Matches Telegram identities to employees and verifies them. Cannot approve.",
}


def ensure_roles():
	for role, _description in ROLES.items():
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def before_install():
	ensure_roles()


def after_migrate():
	ensure_roles()
	ensure_indexes()


def ensure_indexes():
	"""Weekly statistics filter by chat and date: a composite index keeps them fast on a large message table."""
	if frappe.db.table_exists(DT_ACTIVITY):
		frappe.db.add_index(DT_ACTIVITY, ["telegram_chat_id", "activity_date"], "tam_chat_date_index")
