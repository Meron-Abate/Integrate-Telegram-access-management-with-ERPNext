import frappe

from telegram_access_manager.constants import VERIFIER_ROLES


def has_app_permission():
	return bool(set(frappe.get_roles()) & VERIFIER_ROLES)
