import frappe

from telegram_access_manager.constants import ROLE_MANAGER, ROLE_VERIFIER

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
