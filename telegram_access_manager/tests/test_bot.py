import json
import os
from unittest.mock import patch

import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from telegram_access_manager.bot.handlers import CB_REQUEST, dispatch
from telegram_access_manager.bot.ingest import ingest_update, update_key
from telegram_access_manager.constants import DT_AUDIT, DT_CHAT, DT_REQUEST, DT_UPDATE
from telegram_access_manager.services.invites import find_active_link
from telegram_access_manager.tests.utils import FAKE_SECRET, TAMTestCase


def chat_id(name):
	return frappe.db.get_value(DT_CHAT, name, "telegram_chat_id")


class TestBotConversation(TAMTestCase):
	def _msg(self, user, text):
		return {"message": {"message_id": 1, "from": user, "chat": {"id": user["id"], "type": "private"}, "text": text}}

	def test_start_shows_privacy_notice_without_creating_request(self):
		user = self.new_tg_user()
		dispatch(self._msg(user, "/start"))
		text = self.tg.calls_for("sendMessage")[0]["text"]
		self.assertIn("never ask for passwords", text)
		self.assertFalse(frappe.db.exists(DT_REQUEST, {"telegram_user_id": str(user["id"])}))

	def test_request_button_creates_single_request(self):
		user = self.new_tg_user()
		query = {"callback_query": {"id": "q1", "from": user, "data": CB_REQUEST, "message": {"chat": {"id": user["id"], "type": "private"}}}}
		dispatch(query)
		dispatch(query)
		dispatch(self._msg(user, "/request"))
		self.assertEqual(frappe.db.count(DT_REQUEST, {"telegram_user_id": str(user["id"])}), 1)
		self.assertIn("already have an open request", self.tg.calls_for("sendMessage")[-1]["text"])

	def test_duplicate_update_delivery_is_ignored(self):
		user = self.new_tg_user()
		update = {"update_id": user["id"], **self._msg(user, "/request")}
		self.assertEqual(ingest_update(update, enqueue=False), "accepted")
		self.assertEqual(ingest_update(update, enqueue=False), "duplicate")
		self.assertEqual(frappe.db.get_value(DT_UPDATE, update_key(user["id"]), "status"), "Processed")
		self.assertEqual(frappe.db.count(DT_REQUEST, {"telegram_user_id": str(user["id"])}), 1)
		self.assertEqual(len(self.tg.calls_for("sendMessage")), 1)

	def test_invalid_update_rejected(self):
		self.assertEqual(ingest_update({"update_id": "abc"}, enqueue=False), "invalid")
		self.assertEqual(ingest_update({"update_id": True}, enqueue=False), "invalid")

	def test_group_messages_are_ignored(self):
		user = self.new_tg_user()
		handled = dispatch({"message": {"from": user, "chat": {"id": -100123, "type": "supergroup"}, "text": "/request"}})
		self.assertFalse(handled)
		self.assertFalse(frappe.db.exists(DT_REQUEST, {"telegram_user_id": str(user["id"])}))

	def test_status_only_reveals_own_data(self):
		owner = self.new_tg_user()
		self.approved_access(owner)
		stranger = self.new_tg_user()
		self.tg.calls.clear()
		dispatch(self._msg(stranger, "/status"))
		text = self.tg.calls_for("sendMessage")[0]["text"]
		self.assertIn("no access request", text)
		self.assertNotIn(self.chat_prod, text)

		self.tg.calls.clear()
		dispatch(self._msg(owner, "/status"))
		self.assertIn(self.chat_prod, self.tg.calls_for("sendMessage")[0]["text"])

	def test_message_rate_limit(self):
		self.set_settings(max_messages_per_minute=2)
		user = self.new_tg_user()
		for _ in range(4):
			dispatch(self._msg(user, "/help"))
		self.assertEqual(len(self.tg.calls_for("sendMessage")), 2)


class TestJoinRequests(TAMTestCase):
	def _join(self, user, chat, link_name=None):
		return {
			"chat_join_request": {
				"chat": {"id": int(chat_id(chat)), "type": "supergroup"},
				"from": user,
				"user_chat_id": user["id"],
				"date": 0,
				"invite_link": {"name": link_name} if link_name else None,
			}
		}

	def test_join_request_approved_only_for_allowed_chat(self):
		user = self.new_tg_user()
		self.approved_access(user, overrides={self.chat_equip: "Deny"})
		self.tg.calls.clear()

		dispatch(self._join(user, self.chat_prod))
		dispatch(self._join(user, self.chat_equip))  # denied individually
		dispatch(self._join(user, self.chat_finance))  # same community, never granted

		approved = [c["chat_id"] for c in self.tg.calls_for("approveChatJoinRequest")]
		declined = [c["chat_id"] for c in self.tg.calls_for("declineChatJoinRequest")]
		self.assertEqual(approved, [chat_id(self.chat_prod)])
		self.assertEqual(sorted(declined), sorted([chat_id(self.chat_equip), chat_id(self.chat_finance)]))

	def test_join_request_query_is_answered_inline(self):
		user = self.new_tg_user()
		self.approved_access(user)
		self.tg.calls.clear()
		update = self._join(user, self.chat_prod)
		update["chat_join_request"]["query_id"] = "jrq-1"
		update["update_id"] = user["id"]
		with patch("frappe.enqueue") as enqueue:
			self.assertEqual(ingest_update(update, enqueue=True), "accepted")
		enqueue.assert_not_called()  # processed in the webhook request to meet the 10 s deadline
		self.assertEqual(self.tg.calls_for("answerChatJoinRequestQuery"), [{"query_id": "jrq-1", "result": "approve"}])
		self.assertFalse(self.tg.calls_for("approveChatJoinRequest"))

		intruder = self.new_tg_user()
		update = self._join(intruder, self.chat_prod)
		update["chat_join_request"]["query_id"] = "jrq-2"
		dispatch(update)
		self.assertEqual(self.tg.calls_for("answerChatJoinRequestQuery")[-1], {"query_id": "jrq-2", "result": "decline"})

		self.set_settings(decline_unauthorized_join_requests=0)
		update["chat_join_request"]["query_id"] = "jrq-3"
		dispatch(update)
		self.assertEqual(self.tg.calls_for("answerChatJoinRequestQuery")[-1], {"query_id": "jrq-3", "result": "queue"})

	def test_unknown_user_declined(self):
		dispatch(self._join(self.new_tg_user(), self.chat_prod))
		self.assertEqual(len(self.tg.calls_for("declineChatJoinRequest")), 1)
		self.assertFalse(self.tg.calls_for("approveChatJoinRequest"))

	def test_revoked_user_declined(self):
		user = self.new_tg_user()
		_, access = self.approved_access(user)
		access.revoke_access("x")
		self.tg.calls.clear()
		dispatch(self._join(user, self.chat_prod))
		self.assertFalse(self.tg.calls_for("approveChatJoinRequest"))

	def test_unmanaged_chat_is_left_alone(self):
		user = self.new_tg_user()
		self.approved_access(user)
		self.tg.calls.clear()
		handled = dispatch({"chat_join_request": {"chat": {"id": -1009999999999, "type": "supergroup"}, "from": user, "date": 0}})
		self.assertFalse(handled)
		self.assertFalse(self.tg.calls)

	def test_unauthorized_join_through_managed_link_is_removed(self):
		owner = self.new_tg_user()
		_, access = self.approved_access(owner)
		link_name = frappe.db.get_value("Telegram Invite Link", find_active_link(access.name, self.chat_prod), "link_name")
		intruder = self.new_tg_user()
		dispatch(
			{
				"chat_member": {
					"chat": {"id": int(chat_id(self.chat_prod)), "type": "supergroup"},
					"from": intruder,
					"date": 0,
					"old_chat_member": {"status": "left", "user": intruder},
					"new_chat_member": {"status": "member", "user": intruder},
					"invite_link": {"name": link_name},
				}
			}
		)
		self.assertEqual([b["user_id"] for b in self.tg.calls_for("banChatMember")], [intruder["id"]])
		self.assertTrue(frappe.db.exists(DT_AUDIT, {"event_type": "Unauthorized Member", "telegram_user_id": str(intruder["id"])}))

	def test_member_update_records_actual_membership(self):
		user = self.new_tg_user()
		_, access = self.approved_access(user)
		dispatch(
			{
				"chat_member": {
					"chat": {"id": int(chat_id(self.chat_news)), "type": "channel"},
					"from": user,
					"date": 0,
					"old_chat_member": {"status": "left", "user": user},
					"new_chat_member": {"status": "member", "user": user},
				}
			}
		)
		access.reload()
		row = next(r for r in access.chat_permissions if r.telegram_chat == self.chat_news)
		self.assertEqual(row.membership_status, "Member")

	def test_bot_added_to_chat_is_discovered_inactive(self):
		dispatch(
			{
				"my_chat_member": {
					"chat": {"id": -1007777777777, "type": "supergroup", "title": "TAM New Group"},
					"from": self.new_tg_user(),
					"date": 0,
					"old_chat_member": {"status": "left"},
					"new_chat_member": {"status": "administrator", "can_invite_users": True, "can_restrict_members": True},
				}
			}
		)
		chat = frappe.get_doc(DT_CHAT, {"telegram_chat_id": "-1007777777777"})
		self.assertEqual(chat.registration_status, "Discovered")
		self.assertFalse(chat.is_active)
		self.assertEqual(chat.bot_status, "Administrator")


class TestChatRegistration(TAMTestCase):
	def test_chat_id_validation(self):
		for bad in ("abc", "12.5", "", "--100"):
			doc = frappe.get_doc({"doctype": DT_CHAT, "chat_name": f"TAM bad {bad}", "telegram_chat_id": bad, "chat_type": "Supergroup"})
			self.assertRaises(frappe.ValidationError, doc.insert)

	def test_basic_group_uses_single_use_links(self):
		doc = frappe.get_doc(
			{"doctype": DT_CHAT, "chat_name": "TAM Basic", "telegram_chat_id": "-4001", "chat_type": "Group", "access_method": "Join Request"}
		).insert()
		self.assertEqual(doc.access_method, "Single-Use Invite Link")

	def test_validate_bot_access(self):
		doc = frappe.get_doc(DT_CHAT, self.chat_prod)
		result = doc.validate_bot_access()
		self.assertTrue(result["ok"])
		self.assertEqual(frappe.db.get_value(DT_CHAT, self.chat_prod, "invite_link_status"), "Available")


class TestWebhook(TAMTestCase):
	def _call(self, body, secret=None, raw=None):
		from telegram_access_manager.api.webhook import telegram_webhook

		headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
		builder = EnvironBuilder(
			method="POST", data=raw if raw is not None else json.dumps(body), headers=headers, content_type="application/json"
		)
		frappe.local.request = Request(builder.get_environ())
		frappe.local.request_ip = "127.0.0.1"
		frappe.local.response = frappe._dict(http_status_code=200)
		try:
			with patch("frappe.enqueue") as enqueue:
				result = telegram_webhook()
			return result, frappe.local.response.http_status_code, enqueue
		finally:
			frappe.local.request = None

	def test_missing_or_wrong_secret_is_forbidden(self):
		update = {"update_id": 424242001, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "text": "/start"}}
		_, status, enqueue = self._call(update)
		self.assertEqual(status, 403)
		_, status, enqueue = self._call(update, secret="wrong-secret-wrong-secret-wrong-secret")
		self.assertEqual(status, 403)
		enqueue.assert_not_called()
		self.assertFalse(frappe.db.exists(DT_UPDATE, update_key(424242001)))

	def test_unconfigured_secret_refuses_everything(self):
		with (
			patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": ""}),
			patch.object(frappe, "conf", frappe._dict()),
			patch("telegram_access_manager.credentials._settings_secret", return_value=None),
		):
			_, status, _ = self._call({"update_id": 424242002}, secret="")
		self.assertEqual(status, 403)

	def test_valid_update_is_recorded_and_queued(self):
		update = {"update_id": 424242003, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "text": "/start"}}
		result, status, enqueue = self._call(update, secret=FAKE_SECRET)
		self.assertEqual(status, 200)
		self.assertTrue(result["ok"])
		self.assertTrue(frappe.db.exists(DT_UPDATE, update_key(424242003)))
		enqueue.assert_called_once()
		_, _, enqueue = self._call(update, secret=FAKE_SECRET)
		enqueue.assert_not_called()  # duplicate delivery

	def test_invalid_json(self):
		_, status, _ = self._call(None, secret=FAKE_SECRET, raw="{not json")
		self.assertEqual(status, 400)

	def test_disabled_integration_ignores_updates(self):
		self.set_settings(enabled=0)
		result, status, enqueue = self._call({"update_id": 424242004}, secret=FAKE_SECRET)
		self.assertEqual(status, 200)
		self.assertTrue(result.get("ignored"))
		enqueue.assert_not_called()

	def test_admin_api_requires_manager(self):
		from telegram_access_manager.api import admin
		from telegram_access_manager.tests.utils import VERIFIER

		frappe.set_user(VERIFIER)
		self.assertRaises(frappe.PermissionError, admin.test_connection)
		self.assertRaises(frappe.PermissionError, admin.set_webhook)
		self.assertRaises(frappe.PermissionError, admin.delete_webhook)
