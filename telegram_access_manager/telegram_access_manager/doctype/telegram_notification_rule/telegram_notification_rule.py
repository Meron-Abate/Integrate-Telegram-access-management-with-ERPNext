import frappe
from frappe import _
from frappe.model.document import Document

from telegram_access_manager.constants import MANAGER_ROLES
from telegram_access_manager.services import notification_rules
from telegram_access_manager.services.common import require_roles


class TelegramNotificationRule(Document):
	def validate(self):
		meta = frappe.get_meta(self.reference_doctype)
		if self.event == "Submit" and not meta.is_submittable:
			frappe.throw(_("{0} is not submittable, so 'Submit' / 'Cancel' can't be used.").format(self.reference_doctype))
		if self.event == "Cancel" and not meta.is_submittable:
			frappe.throw(_("{0} is not submittable, so 'Submit' / 'Cancel' can't be used.").format(self.reference_doctype))
		if self.event == "Value Change":
			if not self.value_changed or not meta.has_field(self.value_changed):
				frappe.throw(_("'{0}' is not a field of {1}.").format(self.value_changed, self.reference_doctype))
		else:
			self.value_changed = None
		if self.condition:
			try:
				compile(self.condition.strip(), "<condition>", "eval")
			except SyntaxError as e:
				frappe.throw(_("Condition is not a valid Python expression: {0}").format(e))

	def on_update(self):
		notification_rules.clear_cache()

	def on_trash(self):
		notification_rules.clear_cache()

	@frappe.whitelist()
	def send_test(self):
		require_roles(MANAGER_ROLES, "send a test message")
		ok, error = notification_rules.send_text(
			self.telegram_chat, f"✅ Test from ERPNext: rule “{frappe.utils.escape_html(self.name)}” is connected to this chat."
		)
		if not ok:
			frappe.throw(error or _("Could not send the test message."))
		return True
