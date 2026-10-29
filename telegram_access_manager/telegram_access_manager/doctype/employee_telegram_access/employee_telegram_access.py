import frappe
from frappe import _
from frappe.model.document import Document

from telegram_access_manager.constants import MANAGER_ROLES, ROW_ACTION_REQUIRED, ROW_FAILED, VERIFIER_ROLES
from telegram_access_manager.services import access as access_service
from telegram_access_manager.services import permissions
from telegram_access_manager.services.audit import log_for_access
from telegram_access_manager.services.common import field_changed, require_roles, validate_telegram_user_id
from telegram_access_manager.services.sync import enqueue_sync

# Identity, verification and status fields are set by approval/revocation only.
PROTECTED_FIELDS = (
	"employee",
	"telegram_user_id",
	"telegram_username",
	"display_name",
	"is_active",
	"status",
	"review_required",
	"review_reason",
	"source_request",
	"verified_by",
	"verified_on",
	"verification_method",
	"approved_by",
	"sync_status",
	"last_sync_on",
	"last_sync_error",
)
# Child columns that only the system writes (administrators edit telegram_chat and override).
PROTECTED_ROW_FIELDS = ("membership_status", "sync_status", "sync_attempts", "next_retry_on", "last_error", "retryable")


class EmployeeTelegramAccess(Document):
	def validate(self):
		self.telegram_user_id = validate_telegram_user_id(self.telegram_user_id)
		if not self.flags.tam_system_update:
			if self.is_new():
				frappe.throw(_("Employee Telegram Access is created by approving a Telegram Access Request."))
			self._guard_protected_fields()
		if not self.flags.tam_rows_recalculated:
			access_service.recalculate_rows(self)

	def _guard_protected_fields(self):
		before = self.get_doc_before_save()
		if not before:
			return
		for field in PROTECTED_FIELDS:
			if field_changed(self, before, field):
				frappe.throw(_("{0} cannot be edited directly.").format(_(self.meta.get_label(field))), frappe.PermissionError)
		old_rows = {r.name: r for r in before.chat_permissions}
		for row in self.chat_permissions:
			old = old_rows.get(row.name)
			if not old:
				# New rows start from a clean, system-controlled state.
				for field in PROTECTED_ROW_FIELDS:
					row.set(field, None)
				continue
			for field in PROTECTED_ROW_FIELDS:
				row.set(field, old.get(field))

	def on_update(self):
		if self.flags.tam_system_update:
			return
		before = self.get_doc_before_save()
		if before and (
			before.access_role != self.access_role
			or sorted((r.telegram_chat, r.override) for r in before.chat_permissions)
			!= sorted((r.telegram_chat, r.override) for r in self.chat_permissions)
		):
			log_for_access(self, "Access Updated", "Info", _("Role/overrides changed by administrator; changes pending"))

	# ------------------------------------------------------------------------------- actions
	@frappe.whitelist()
	def get_effective_permissions_preview(self):
		require_roles(VERIFIER_ROLES, _("view Telegram access"))
		resolved = permissions.compute_for_rows(self.access_role, self.chat_permissions)
		rows = permissions.preview_rows(resolved)
		state = {r.telegram_chat: r for r in self.chat_permissions}
		for row in rows:
			saved = state.get(row["telegram_chat"])
			if not self.is_active:
				row["effective"], row["note"] = "Deny", _("Access revoked")
			row["membership_status"] = saved.membership_status if saved else "Unknown"
			row["sync_status"] = saved.sync_status if saved else "Pending"
			row["last_error"] = saved.last_error if saved else None
		return rows

	def _require_saved(self):
		self.load_from_db()

	@frappe.whitelist(methods=["POST"])
	def apply_to_telegram(self):
		"""Apply desired permissions after the administrator has reviewed them."""
		require_roles(MANAGER_ROLES, _("apply Telegram access changes"))
		self._require_saved()
		enqueue_sync(self.name, mode="manual", context="update")
		return True

	@frappe.whitelist(methods=["POST"])
	def refresh_membership(self):
		require_roles(MANAGER_ROLES, _("refresh Telegram membership"))
		self._require_saved()
		enqueue_sync(self.name, mode="all", context="refresh", notify=False)
		return True

	@frappe.whitelist(methods=["POST"])
	def retry_failed(self):
		require_roles(MANAGER_ROLES, _("retry Telegram synchronization"))
		self._require_saved()
		if not any(r.sync_status in (ROW_FAILED, ROW_ACTION_REQUIRED) for r in self.chat_permissions):
			frappe.throw(_("There are no failed chats to retry."))
		enqueue_sync(self.name, mode="manual", context="retry")
		return True

	@frappe.whitelist(methods=["POST"])
	def revoke_access(self, reason: str | None = None, notify: int = 1):
		require_roles(MANAGER_ROLES, _("revoke Telegram access"))
		self._require_saved()
		if not self.is_active:
			frappe.throw(_("Access is already revoked."))
		access_service.revoke_access(self.name, reason=(reason or "").strip()[:1000] or None, notify=bool(int(notify)))
		return True

	@frappe.whitelist(methods=["POST"])
	def restore_access(self):
		require_roles(MANAGER_ROLES, _("restore Telegram access"))
		self._require_saved()
		if self.is_active:
			frappe.throw(_("Access is already active."))
		if frappe.db.get_value("Employee", self.employee, "status") != "Active":
			frappe.throw(_("Employee {0} is not active.").format(self.employee))
		access_service.restore_access(self.name)
		return True
