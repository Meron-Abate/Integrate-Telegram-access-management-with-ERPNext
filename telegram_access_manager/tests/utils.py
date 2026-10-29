"""Test helpers. Telegram is always mocked: no network access, no real bot, no real messages."""

import os
import random
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_years, getdate

from telegram_access_manager.constants import DT_CHAT, DT_ROLE, ROLE_MANAGER, ROLE_VERIFIER, SETTINGS
from telegram_access_manager.telegram.client import TelegramAPIError

# Obviously fake placeholder values matching the expected formats.
FAKE_TOKEN = "123456789:TEST_ONLY_not_a_real_token_xxxxxxxxxx"
FAKE_SECRET = "test-only-webhook-secret-0123456789abcdef"

MANAGER = "tam-test-manager@example.com"
VERIFIER = "tam-test-verifier@example.com"
OUTSIDER = "tam-test-outsider@example.com"


class FakeTelegram:
	"""Records calls and simulates Telegram state. Inject failures with ``fail[method] = [error, ...]``."""

	BOT_ID = 999000

	def __init__(self):
		self.calls: list[tuple[str, dict]] = []
		self.members: dict[tuple[str, int], str] = {}
		self.fail: dict[str, list] = {}
		self._links = 0
		# What getChatMember returns for the bot itself (override per test to simulate missing rights).
		self.bot_member = {"status": "administrator", "can_invite_users": True, "can_restrict_members": True}

	def _record(self, method, **kwargs):
		self.calls.append((method, kwargs))
		queue = self.fail.get(method)
		if queue:
			raise queue.pop(0)

	def calls_for(self, method):
		return [kw for m, kw in self.calls if m == method]

	def set_member(self, chat_id, user_id, status="member"):
		self.members[(str(chat_id), int(user_id))] = status

	def status_of(self, chat_id, user_id):
		return self.members.get((str(chat_id), int(user_id)), "left")

	# --- API surface used by the app ---
	def get_me(self):
		self._record("getMe")
		return {"id": self.BOT_ID, "username": "tam_test_bot", "can_join_groups": True}

	def get_chat(self, chat_id):
		self._record("getChat", chat_id=chat_id)
		return {"id": int(chat_id), "type": "supergroup", "title": f"Chat {chat_id}"}

	def get_chat_member(self, chat_id, user_id):
		self._record("getChatMember", chat_id=chat_id, user_id=user_id)
		if int(user_id) == self.BOT_ID:
			return dict(self.bot_member)
		return {"status": self.status_of(chat_id, user_id)}

	def create_chat_invite_link(self, chat_id, name, expire_date, member_limit=None, creates_join_request=None):
		self._record(
			"createChatInviteLink",
			chat_id=chat_id,
			name=name,
			expire_date=expire_date,
			member_limit=member_limit,
			creates_join_request=creates_join_request,
		)
		self._links += 1
		return {"invite_link": f"https://t.me/+FAKE{self._links:04d}{str(chat_id)[-4:]}", "name": name}

	def revoke_chat_invite_link(self, chat_id, invite_link):
		self._record("revokeChatInviteLink", chat_id=chat_id, invite_link=invite_link)
		return {}

	def approve_chat_join_request(self, chat_id, user_id):
		self._record("approveChatJoinRequest", chat_id=chat_id, user_id=user_id)
		self.set_member(chat_id, user_id)
		return True

	def answer_chat_join_request_query(self, query_id, result):
		self._record("answerChatJoinRequestQuery", query_id=query_id, result=result)
		return True

	def decline_chat_join_request(self, chat_id, user_id):
		self._record("declineChatJoinRequest", chat_id=chat_id, user_id=user_id)
		return True

	def ban_chat_member(self, chat_id, user_id):
		self._record("banChatMember", chat_id=chat_id, user_id=user_id)
		self.set_member(chat_id, user_id, "kicked")
		return True

	def unban_chat_member(self, chat_id, user_id):
		self._record("unbanChatMember", chat_id=chat_id, user_id=user_id)
		if self.status_of(chat_id, user_id) == "kicked":
			self.set_member(chat_id, user_id, "left")
		return True

	def send_message(self, chat_id, text, reply_markup=None):
		self._record("sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup)
		return {"message_id": len(self.calls)}

	def delete_message(self, chat_id, message_id):
		self._record("deleteMessage", chat_id=chat_id, message_id=message_id)
		return True

	def answer_callback_query(self, callback_query_id, text=None):
		self._record("answerCallbackQuery", callback_query_id=callback_query_id)
		return True

	def set_webhook(self, url, secret_token, allowed_updates, drop_pending_updates=False):
		self._record("setWebhook", url=url, allowed_updates=allowed_updates)
		return True

	def delete_webhook(self, drop_pending_updates=False):
		self._record("deleteWebhook")
		return True

	def get_webhook_info(self):
		self._record("getWebhookInfo")
		return {"url": "", "pending_update_count": 0}


def api_error(method, code, description, retry_after=None):
	return TelegramAPIError(method, code, description, retry_after=retry_after)


def _ensure_user(email, roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.set("roles", [])
	user.add_roles(*roles)


class TAMTestCase(IntegrationTestCase):
	"""Base class. Base fixtures are created once per class; each test runs inside a savepoint."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		_ensure_user(MANAGER, [ROLE_MANAGER])
		_ensure_user(VERIFIER, [ROLE_VERIFIER])
		_ensure_user(OUTSIDER, ["Employee"] if frappe.db.exists("Role", "Employee") else [])

		cls.emp_a = cls.make_employee("Abebe", "TAMTest")
		cls.emp_b = cls.make_employee("Bethlehem", "TAMTest")
		cls.community = cls.make_community("TAM Test Community")
		cls.chat_prod = cls.make_chat("TAM Production Group", "-1009000000001")
		cls.chat_equip = cls.make_chat("TAM Equipment Group", "-1009000000002")
		cls.chat_news = cls.make_chat("TAM Announcements Channel", "-1009000000003", "Channel")
		cls.chat_mgmt = cls.make_chat("TAM Management Channel", "-1009000000004", "Channel")
		cls.chat_finance = cls.make_chat("TAM Finance Group", "-1009000000005")
		cls.role_prod = cls.make_role("TAM Production Team", [cls.chat_prod, cls.chat_equip, cls.chat_news])

	@classmethod
	def make_employee(cls, first, last):
		emp = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": first,
				"last_name": last,
				"gender": "Female" if first.endswith("m") else "Male",
				"date_of_birth": add_years(getdate(), -30),
				"date_of_joining": add_years(getdate(), -1),
				"company": cls.company,
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		return emp.name

	@classmethod
	def make_community(cls, name):
		return frappe.get_doc({"doctype": "Telegram Community", "community_name": name}).insert().name

	@classmethod
	def make_chat(cls, name, chat_id, chat_type="Supergroup", active=1):
		doc = frappe.get_doc(
			{
				"doctype": DT_CHAT,
				"chat_name": name,
				"telegram_chat_id": chat_id,
				"chat_type": chat_type,
				"community": cls.community,
				"is_active": active,
				"bot_status": "Administrator",
			}
		)
		doc.flags.ignore_messages = True
		return doc.insert().name

	@classmethod
	def make_role(cls, name, chats):
		return frappe.get_doc(
			{"doctype": DT_ROLE, "role_name": name, "chats": [{"telegram_chat": c} for c in chats]}
		).insert().name

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("tam_test")
		self.tg = FakeTelegram()
		patcher = patch("telegram_access_manager.telegram.get_client", return_value=self.tg)
		patcher.start()
		self.addCleanup(patcher.stop)
		env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": FAKE_TOKEN, "TELEGRAM_WEBHOOK_SECRET": FAKE_SECRET})
		env.start()
		self.addCleanup(env.stop)
		self.set_settings(
			enabled=1,
			update_mode="Webhook",
			require_separate_verifier=0,
			auto_apply_role_changes=0,
			auto_revoke_on_employee_exit=0,
			remove_member_on_revoke=1,
			ban_on_removal=0,
			decline_unauthorized_join_requests=1,
			remove_unauthorized_joins=1,
			max_requests_per_user_per_day=3,
			max_messages_per_minute=50,
			# Never use the real bot identity stored on the site.
			bot_id=str(FakeTelegram.BOT_ID),
			bot_username="tam_test_bot",
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="tam_test")
		frappe.clear_document_cache(SETTINGS, SETTINGS)

	def set_settings(self, **values):
		doc = frappe.get_single(SETTINGS)
		doc.update(values)
		doc.save(ignore_permissions=True)
		frappe.clear_document_cache(SETTINGS, SETTINGS)

	def new_tg_user(self, **extra):
		uid = random.randint(10**9, 9 * 10**9)  # unique per test; also keeps redis throttling keys apart
		return {"id": uid, "is_bot": False, "first_name": "Test", "last_name": "User", "username": f"user{uid}", **extra}

	def approved_access(self, tg_user=None, employee=None, role=None, overrides=None):
		"""Run the full request -> verify -> approve flow and return (request, access) docs."""
		from telegram_access_manager.services.requests import submit_request_from_telegram

		tg_user = tg_user or self.new_tg_user()
		_outcome, name = submit_request_from_telegram(tg_user)
		req = frappe.get_doc("Telegram Access Request", name)
		req.employee = employee or self.emp_a
		req.access_role = role if role is not None else self.role_prod
		for chat, override in (overrides or {}).items():
			req.append("chat_permissions", {"telegram_chat": chat, "override": override})
		req.save()
		result = req.approve()
		req.reload()
		access = frappe.get_doc("Employee Telegram Access", result["employee_telegram_access"])
		return req, access
