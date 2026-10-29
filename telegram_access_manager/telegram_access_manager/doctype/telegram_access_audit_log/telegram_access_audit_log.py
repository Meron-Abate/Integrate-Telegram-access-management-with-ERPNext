import frappe
from frappe import _
from frappe.model.document import Document


class TelegramAccessAuditLog(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("Audit log entries cannot be modified."))
