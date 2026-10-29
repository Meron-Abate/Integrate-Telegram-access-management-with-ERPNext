import frappe
from frappe import _
from frappe.model.document import Document


class TelegramCommunity(Document):
	def validate(self):
		link = (self.community_link or "").lower()
		if "/addlist/" in link:
			frappe.msgprint(
				_(
					"This looks like a chat folder link. Never share it with employees: it lets anyone join every chat in "
					"the folder and bypasses the per-chat permissions managed here."
				),
				indicator="orange",
				title=_("Folder link"),
			)
