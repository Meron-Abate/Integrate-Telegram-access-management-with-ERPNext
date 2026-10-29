import frappe
from frappe import _
from frappe.model.document import Document

from telegram_access_manager.constants import DT_ACCESS, MANAGER_ROLES
from telegram_access_manager.services import access as access_service
from telegram_access_manager.services.audit import log_event
from telegram_access_manager.services.common import require_roles, run_or_enqueue


class TelegramAccessRole(Document):
	def validate(self):
		seen = set()
		for row in self.chats:
			if row.telegram_chat in seen:
				frappe.throw(_("Row {0}: Telegram Chat {1} is listed more than once.").format(row.idx, frappe.bold(row.telegram_chat)))
			seen.add(row.telegram_chat)

	def onload(self):
		self.set_onload(
			"affected",
			{
				"employees": frappe.db.count(DT_ACCESS, {"access_role": self.name, "is_active": 1}),
				"pending": frappe.db.count(DT_ACCESS, {"access_role": self.name, "sync_status": "Changes Pending"}),
			},
		)

	def on_update(self):
		before = self.get_doc_before_save()
		if not before:
			return
		old, new = {r.telegram_chat for r in before.chats}, {r.telegram_chat for r in self.chats}
		if old == new and before.is_active == self.is_active:
			return
		details = []
		if added := sorted(new - old):
			details.append("Added: " + ", ".join(added))
		if removed := sorted(old - new):
			details.append("Removed: " + ", ".join(removed))
		if before.is_active != self.is_active:
			details.append("Active" if self.is_active else "Deactivated")
		log_event("Role Updated", "Info", f"{self.name}: " + "; ".join(details))
		run_or_enqueue("telegram_access_manager.services.access.recalculate_for_role", role=self.name)

	@frappe.whitelist(methods=["POST"])
	def apply_pending_changes(self):
		require_roles(MANAGER_ROLES, _("apply Telegram access changes"))
		return access_service.apply_pending_changes(self.name)
