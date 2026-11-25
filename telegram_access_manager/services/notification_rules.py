"""Send ERPNext document events to Telegram groups according to *Telegram Notification Rule* records."""

import frappe
from frappe import _
from frappe.utils import escape_html, get_url_to_form, now_datetime
from frappe.utils.safe_exec import safe_eval

from telegram_access_manager import telegram as tg
from telegram_access_manager.credentials import TelegramConfigError
from telegram_access_manager.telegram.client import TelegramAPIError

CACHE_KEY = "tam_notification_rules"
RULE = "Telegram Notification Rule"
EVENT_HOOKS = {
	"after_insert": "New",
	"on_update": "Save",
	"on_submit": "Submit",
	"on_cancel": "Cancel",
}
SKIP_DOCTYPES = frozenset(
	{RULE, "Telegram Chat", "Error Log", "Activity Log", "Version", "Comment", "Access Log", "Scheduled Job Log"}
)


def clear_cache():
	frappe.cache.delete_value(CACHE_KEY)


def _rules_by_doctype() -> dict[str, list[dict]]:
	cached = frappe.cache.get_value(CACHE_KEY)
	if cached is None:
		cached = {}
		for r in frappe.get_all(
			RULE, filters={"enabled": 1}, fields=["name", "reference_doctype", "event", "value_changed", "condition"]
		):
			cached.setdefault(r.reference_doctype, []).append(dict(r))
		frappe.cache.set_value(CACHE_KEY, cached)
	return cached


def handle_doc_event(doc, method=None, *args, **kwargs):
	"""doc_events hook for every DocType. Must never break the save that triggered it."""
	try:
		if frappe.flags.in_install or frappe.flags.in_migrate or doc.doctype in SKIP_DOCTYPES:
			return
		rules = _rules_by_doctype().get(doc.doctype)
		if not rules:
			return
		event = EVENT_HOOKS.get(method)
		for rule in rules:
			if rule["event"] == "Value Change":
				if method != "on_update" or doc.flags.in_insert or not doc.has_value_changed(rule["value_changed"]):
					continue
			elif rule["event"] != event or (rule["event"] == "Save" and doc.flags.in_insert):
				continue
			if rule["condition"] and not _condition_met(rule, doc):
				continue
			frappe.enqueue(
				"telegram_access_manager.services.notification_rules.deliver",
				queue="short",
				enqueue_after_commit=True,
				rule=rule["name"],
				doctype=doc.doctype,
				docname=doc.name,
			)
	except Exception:
		frappe.log_error(title="Telegram notification rule failed", message=frappe.get_traceback())


def _condition_met(rule: dict, doc) -> bool:
	try:
		return bool(safe_eval(rule["condition"], None, {"doc": doc.as_dict(), "frappe": frappe._dict(utils=frappe.utils)}))
	except Exception:
		frappe.log_error(title=f"Telegram rule condition error: {rule['name']}", message=frappe.get_traceback())
		return False


def deliver(rule: str, doctype: str, docname: str):
	"""Background job: render and send one notification, recording the outcome on the rule."""
	rule_doc = frappe.get_doc(RULE, rule)
	if not rule_doc.enabled:
		return
	try:
		doc = frappe.get_doc(doctype, docname)
	except frappe.DoesNotExistError:
		return
	text = render_message(rule_doc, doc)
	ok, error = send_text(rule_doc.telegram_chat, text)
	frappe.db.set_value(
		RULE,
		rule,
		{"last_sent_on": now_datetime() if ok else rule_doc.last_sent_on, "last_error": None if ok else error},
		update_modified=False,
	)


def render_message(rule, doc) -> str:
	if rule.message_template:
		# Escape every substituted value: Telegram parses the result as HTML.
		body = frappe.render_template(rule.message_template, {"doc": _Escaped(doc.as_dict()), "frappe": frappe})
	else:
		body = f"<b>{escape_html(_(doc.doctype))}</b> {escape_html(doc.get('title') or doc.name)}\n{escape_html(_(rule.event))}"
	if rule.include_link:
		body += f'\n<a href="{escape_html(get_url_to_form(doc.doctype, doc.name))}">{_("Open in ERP")}</a>'
	return body[:4000]


class _Escaped(dict):
	"""dict whose string values are HTML-escaped on access (attribute or item)."""

	def __getattr__(self, key):
		return self[key] if key in self else None

	def __getitem__(self, key):
		value = dict.__getitem__(self, key)
		return escape_html(value) if isinstance(value, str) else value


def send_text(chat: str, text: str) -> tuple[bool, str | None]:
	chat_id = frappe.db.get_value("Telegram Chat", chat, "telegram_chat_id")
	if not chat_id:
		return False, _("Telegram Chat {0} has no chat ID.").format(chat)
	try:
		tg.get_client().send_message(chat_id, text)
		return True, None
	except TelegramAPIError as e:
		return False, e.description
	except TelegramConfigError as e:
		return False, str(e)
