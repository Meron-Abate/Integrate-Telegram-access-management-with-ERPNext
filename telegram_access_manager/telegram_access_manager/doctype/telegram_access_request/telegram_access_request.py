import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from telegram_access_manager.constants import (
	ALLOW,
	CLOSED_REQUEST_STATUSES,
	MANAGER_ROLES,
	OPEN_REQUEST_STATUSES,
	REQ_APPROVED,
	REQ_CANCELLED,
	REQ_ERROR,
	REQ_PENDING_APPROVAL,
	REQ_PENDING_VERIFICATION,
	REQ_REJECTED,
	VERIFIER_ROLES,
)
from telegram_access_manager.services import access as access_service
from telegram_access_manager.services import notifications, permissions
from telegram_access_manager.services.audit import log_error, log_event
from telegram_access_manager.services.common import (
	field_changed,
	get_settings,
	require_roles,
	validate_telegram_user_id,
)
from telegram_access_manager.services.sync import enqueue_sync

# Verification = an authorized user confirmed the linked Employee; no separate reason is asked for.
VERIFICATION_METHOD = "Employee linked in ERPNext"

# Only changed by the controller's own workflow methods / the bot, never by form edits.
PROTECTED_FIELDS = (
	"status",
	"verification_status",
	"verification_method",
	"verified_by",
	"verified_on",
	"verification_notes",
	"decided_by",
	"decision_on",
	"notification_status",
	"employee_telegram_access",
	"error_details",
	"request_source",
	"request_timestamp",
	"telegram_username",
	"telegram_display_name",
	"telegram_language",
)
DECISION_FIELDS = ("employee", "access_role", "chat_permissions")


class TelegramAccessRequest(Document):
	def validate(self):
		self.telegram_user_id = validate_telegram_user_id(self.telegram_user_id)
		system = self.flags.tam_system_update
		if not system:
			self._guard_protected_fields()
		self._handle_employee_change()
		self.refresh_permission_rows()

	def _guard_protected_fields(self):
		if self.is_new():
			# Manually created requests start at the beginning of the workflow.
			for field in PROTECTED_FIELDS:
				self.set(field, None)
			self.status = REQ_PENDING_VERIFICATION
			self.verification_status = "Not Verified"
			self.notification_status = "Not Sent"
			self.request_source = "Manual"
			self.request_timestamp = now_datetime()
			return

		before = self.get_doc_before_save()
		if not before:
			return
		for field in PROTECTED_FIELDS:
			if field_changed(self, before, field):
				frappe.throw(
					_("{0} can only be changed through the request actions.").format(_(self.meta.get_label(field))),
					frappe.PermissionError,
				)
		if before.status in CLOSED_REQUEST_STATUSES:
			if self._decision_fields_changed(before):
				frappe.throw(_("This request is {0} and can no longer be changed.").format(_(before.status)))

	def _decision_fields_changed(self, before) -> bool:
		if (before.employee or None) != (self.employee or None) or (before.access_role or None) != (self.access_role or None):
			return True
		key = lambda rows: sorted((r.telegram_chat, r.override or "Inherit") for r in rows)  # noqa: E731
		return key(before.chat_permissions) != key(self.chat_permissions)

	def _handle_employee_change(self):
		"""Verification is tied to a specific employee; changing the employee requires re-verification."""
		if self.is_new() or self.status in CLOSED_REQUEST_STATUSES:
			return
		before = self.get_doc_before_save()
		if before and (before.employee or None) != (self.employee or None) and self.verification_status == "Verified":
			self.verification_status = "Not Verified"
			self.verified_by = self.verified_on = self.verification_method = self.verification_notes = None
			self.status = REQ_PENDING_VERIFICATION
			frappe.msgprint(_("Employee changed: identity verification has been reset."), alert=True)

	# --------------------------------------------------------------------------- permissions
	def refresh_permission_rows(self, keep_blank_rows=False):
		"""Rebuild source/effective columns from the database (client values for them are ignored)."""
		resolved = permissions.compute_for_rows(self.access_role, self.chat_permissions)
		blank = [r for r in self.chat_permissions if not r.telegram_chat] if keep_blank_rows else []
		self.set("chat_permissions", [])
		for chat, r in resolved.items():
			if r["source"] == "None" and r["override"] == "Inherit":
				continue  # a role chat that is no longer in the role: drop it
			self.append(
				"chat_permissions",
				{
					"telegram_chat": chat,
					"source": r["source"],
					"override": r["override"],
					"effective_permission": r["effective"],
					"note": r["note"],
				},
			)
		for row in blank:  # rows the user just added and hasn't picked a chat for yet
			self.append("chat_permissions", {"override": row.override or "Allow"})
		return resolved

	@frappe.whitelist()
	def refresh_permissions(self):
		"""Called by the form when the role or overrides change (unsaved document)."""
		require_roles(VERIFIER_ROLES, _("review access requests"))
		if self.status in CLOSED_REQUEST_STATUSES:
			return self.get_effective_permissions_preview()
		self.refresh_permission_rows(keep_blank_rows=True)
		return self.get_effective_permissions_preview()

	@frappe.whitelist()
	def get_effective_permissions_preview(self):
		require_roles(VERIFIER_ROLES, _("review access requests"))
		resolved = permissions.compute_for_rows(self.access_role, self.chat_permissions)
		return permissions.preview_rows(resolved)

	# --------------------------------------------------------------------------- workflow
	def _reload_for_action(self):
		# Actions operate on the saved document, never on unsaved client-side values.
		self.load_from_db()

	def _save_system(self):
		self.flags.tam_system_update = True
		try:
			self.save()
		finally:
			# Never let the bypass leak into a later save of the same object.
			self.flags.tam_system_update = False

	def _validate_employee(self):
		if not self.employee:
			frappe.throw(_("Select the Employee this Telegram identity belongs to."))
		status = frappe.db.get_value("Employee", self.employee, "status")
		if status != "Active":
			frappe.throw(_("Employee {0} is {1}. Only active employees can be granted access.").format(self.employee, status))
		if conflict := access_service.find_identity_conflicts(self.employee, self.telegram_user_id):
			frappe.throw(conflict)

	def _mark_verified(self):
		self.verification_status = "Verified"
		self.verification_method = VERIFICATION_METHOD
		self.verified_by = frappe.session.user
		self.verified_on = now_datetime()
		self.status = REQ_PENDING_APPROVAL
		log_event(
			"Identity Verified",
			"Success",
			f"Employee {self.employee} linked and confirmed by {frappe.session.user}",
			employee=self.employee,
			access_request=self.name,
			telegram_user_id=self.telegram_user_id,
		)

	@frappe.whitelist(methods=["POST"])
	def verify_identity(self):
		"""For verifiers who can't approve: confirm the linked Employee so a manager can approve."""
		require_roles(VERIFIER_ROLES, _("verify Telegram identities"))
		self._reload_for_action()
		if self.status != REQ_PENDING_VERIFICATION:
			frappe.throw(_("Only requests pending verification can be verified."))
		self._validate_employee()
		self._mark_verified()
		self._save_system()
		return self.status

	@frappe.whitelist(methods=["POST"])
	def approve(self, admin_notes: str | None = None):
		require_roles(MANAGER_ROLES, _("approve Telegram access"))
		self._reload_for_action()
		if self.status not in (REQ_PENDING_VERIFICATION, REQ_PENDING_APPROVAL, REQ_ERROR):
			frappe.throw(_("Only open requests can be approved."))
		self._validate_employee()
		separate_verifier = get_settings().require_separate_verifier
		if self.verification_status != "Verified":
			if separate_verifier:
				frappe.throw(_("Another user must verify the linked Employee before approval."), frappe.PermissionError)
			# Linking the Employee and approving is the verification: recorded against the approver.
			self._mark_verified()
		elif separate_verifier and self.verified_by == frappe.session.user:
			frappe.throw(_("Approval must be done by a different user than the verifier."), frappe.PermissionError)

		self.refresh_permission_rows()
		if not any(r.effective_permission == ALLOW for r in self.chat_permissions):
			frappe.throw(_("No chat would be granted. Select a Telegram Access Role or allow at least one chat."))
		if admin_notes:
			self.admin_notes = admin_notes

		savepoint = "tam_approve"
		frappe.db.savepoint(savepoint)
		try:
			access = access_service.create_or_update_access_from_request(self)
		except frappe.ValidationError:
			raise
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			log_error("Telegram access approval failed", frappe.get_traceback())
			self.status = REQ_ERROR
			self.error_details = str(e)[:500]
			self._save_system()
			log_event("Request Error", "Failed", str(e), employee=self.employee, access_request=self.name)
			return {"status": self.status}

		self.status = REQ_APPROVED
		self.employee_telegram_access = access.name
		self.decided_by = frappe.session.user
		self.decision_on = now_datetime()
		self.error_details = None
		self._save_system()
		allowed = [r.telegram_chat for r in self.chat_permissions if r.effective_permission == ALLOW]
		log_event(
			"Request Approved",
			"Success",
			"Allowed chats: " + ", ".join(allowed),
			employee=self.employee,
			employee_telegram_access=access.name,
			access_request=self.name,
			telegram_user_id=self.telegram_user_id,
		)
		enqueue_sync(access.name, mode="pending", context="approval", request=self.name)
		return {"status": self.status, "employee_telegram_access": access.name}

	def _close(self, status, reason, event):
		self._reload_for_action()
		if self.status not in OPEN_REQUEST_STATUSES:
			frappe.throw(_("Only open requests can be {0}.").format(_(status).lower()))
		self.status = status
		self.decided_by = frappe.session.user
		self.decision_on = now_datetime()
		if reason:
			self.admin_notes = "\n".join(filter(None, [self.admin_notes, reason.strip()[:1000]]))
		if status == REQ_REJECTED and self.verification_status == "Not Verified":
			self.verification_status = "Failed"
		self._save_system()
		log_event(event, "Info", reason, employee=self.employee, access_request=self.name, telegram_user_id=self.telegram_user_id)
		self._notify_closed()
		return self.status

	@frappe.whitelist(methods=["POST"])
	def reject(self, reason: str | None = None):
		require_roles(MANAGER_ROLES, _("reject Telegram access requests"))
		return self._close(REQ_REJECTED, reason, "Request Rejected")

	@frappe.whitelist(methods=["POST"])
	def cancel_request(self, reason: str | None = None):
		require_roles(MANAGER_ROLES, _("cancel Telegram access requests"))
		return self._close(REQ_CANCELLED, reason, "Request Cancelled")

	def _notify_closed(self):
		if not get_settings().notify_on_rejection:
			frappe.db.set_value(self.doctype, self.name, "notification_status", "Not Required")
			return
		sent, error = notifications.send(self.telegram_user_id, notifications.rejection_message(self.status))
		frappe.db.set_value(
			self.doctype, self.name, {"notification_status": "Sent" if sent else "Failed", "error_details": error}
		)
		log_event(
			"Notification Sent" if sent else "Notification Failed",
			"Success" if sent else "Failed",
			error,
			employee=self.employee,
			access_request=self.name,
			telegram_user_id=self.telegram_user_id,
		)

	@frappe.whitelist(methods=["POST"])
	def retry_notification(self):
		require_roles(MANAGER_ROLES, _("resend Telegram notifications"))
		self._reload_for_action()
		if self.status == REQ_APPROVED and self.employee_telegram_access:
			enqueue_sync(self.employee_telegram_access, mode="resend", context="approval", request=self.name)
		elif self.status in (REQ_REJECTED, REQ_CANCELLED):
			self._notify_closed()
		else:
			frappe.throw(_("There is no decision to notify yet."))
		return True

