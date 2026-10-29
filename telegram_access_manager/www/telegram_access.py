"""Page controller for the Telegram Access portal (/telegram-access)."""

import frappe
from frappe import _

from telegram_access_manager.constants import VERIFIER_ROLES

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/telegram-access"
		raise frappe.Redirect
	if not set(frappe.get_roles()) & VERIFIER_ROLES:
		frappe.throw(_("You don't have access to Telegram Access. Ask an administrator for the Telegram Access Manager role."), frappe.PermissionError)
	csrf_token = frappe.sessions.get_csrf_token()
	frappe.db.commit()  # persist the CSRF token created for this session
	context.boot = {"csrf_token": csrf_token, "site_name": frappe.local.site}
