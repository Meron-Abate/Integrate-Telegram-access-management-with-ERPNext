import os
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from telegram_access_manager.constants import DT_ACCESS, DT_INVITE, DT_ROLE
from telegram_access_manager.credentials import TelegramConfigError
from telegram_access_manager.services.sync import backoff_minutes, retry_failed_syncs, sync_access
from telegram_access_manager.tests.utils import TAMTestCase, api_error


def chat_id(name):
	return frappe.db.get_value("Telegram Chat", name, "telegram_chat_id")


def row_for(access, chat):
	return next(r for r in frappe.get_doc(DT_ACCESS, access.name).chat_permissions if r.telegram_chat == chat)


class TestRevocation(TAMTestCase):
	def test_revoke_removes_members_and_revokes_links(self):
		user = self.new_tg_user()
		_, access = self.approved_access(user)
		self.tg.set_member(chat_id(self.chat_prod), user["id"])  # employee joined Production
		self.tg.calls.clear()

		access.revoke_access("Left the company")
		access.reload()
		self.assertEqual(access.status, "Revoked")
		self.assertFalse(access.is_active)
		self.assertTrue(all(r.effective_permission == "Deny" for r in access.chat_permissions))

		bans = self.tg.calls_for("banChatMember")
		self.assertEqual([b["chat_id"] for b in bans], [chat_id(self.chat_prod)])
		self.assertEqual(len(self.tg.calls_for("unbanChatMember")), 1)  # kick, not permanent ban
		self.assertEqual(row_for(access, self.chat_prod).membership_status, "Removed")
		self.assertEqual(len(self.tg.calls_for("revokeChatInviteLink")), 3)
		self.assertFalse(frappe.db.exists(DT_INVITE, {"employee_telegram_access": access.name, "status": "Active"}))
		self.assertIn("revoked", self.tg.calls_for("sendMessage")[-1]["text"])

	def test_ban_on_removal_keeps_ban(self):
		self.set_settings(ban_on_removal=1)
		user = self.new_tg_user()
		_, access = self.approved_access(user)
		self.tg.set_member(chat_id(self.chat_prod), user["id"])
		access.revoke_access("x")
		self.assertFalse(self.tg.calls_for("unbanChatMember"))
		self.assertEqual(row_for(access, self.chat_prod).membership_status, "Banned")

	def test_chat_admin_requires_manual_action(self):
		user = self.new_tg_user()
		_, access = self.approved_access(user)
		self.tg.set_member(chat_id(self.chat_prod), user["id"], "administrator")
		access.revoke_access("x")
		self.assertFalse(self.tg.calls_for("banChatMember"))
		self.assertEqual(row_for(access, self.chat_prod).sync_status, "Action Required")

	def test_individual_deny_after_approval_removes_only_that_chat(self):
		user = self.new_tg_user()
		_, access = self.approved_access(user)
		for chat in (self.chat_prod, self.chat_equip):
			self.tg.set_member(chat_id(chat), user["id"])
		access.reload()
		next(r for r in access.chat_permissions if r.telegram_chat == self.chat_equip).override = "Deny"
		access.save()
		self.assertEqual(access.sync_status, "Changes Pending")
		self.assertFalse(self.tg.calls_for("banChatMember"), "nothing changes in Telegram before it is applied")

		access.apply_to_telegram()
		self.assertEqual([b["chat_id"] for b in self.tg.calls_for("banChatMember")], [chat_id(self.chat_equip)])
		self.assertEqual(row_for(access, self.chat_prod).membership_status, "Invite Sent", "unchanged rows are not touched")
		self.assertEqual(row_for(access, self.chat_equip).membership_status, "Removed")
		access.refresh_membership()  # re-reads actual membership from Telegram for every row
		self.assertEqual(row_for(access, self.chat_prod).membership_status, "Member")
		self.assertEqual(row_for(access, self.chat_equip).membership_status, "Not a Member")

	def test_employee_leaving_flags_for_review(self):
		_, access = self.approved_access(self.new_tg_user())
		emp = frappe.get_doc("Employee", self.emp_a)
		emp.status = "Left"
		emp.relieving_date = frappe.utils.today()
		emp.save()
		access.reload()
		self.assertTrue(access.review_required)
		self.assertTrue(access.is_active, "revocation stays an administrator decision by default")

	def test_employee_leaving_auto_revoke_when_enabled(self):
		self.set_settings(auto_revoke_on_employee_exit=1)
		_, access = self.approved_access(self.new_tg_user())
		emp = frappe.get_doc("Employee", self.emp_a)
		emp.status = "Left"
		emp.relieving_date = frappe.utils.today()
		emp.save()
		self.assertFalse(frappe.db.get_value(DT_ACCESS, access.name, "is_active"))


class TestRoleRecalculation(TAMTestCase):
	def test_role_change_recalculates_without_touching_telegram(self):
		user = self.new_tg_user()
		_, access = self.approved_access(user)
		self.tg.set_member(chat_id(self.chat_news), user["id"])
		self.tg.calls.clear()

		role = frappe.get_doc(DT_ROLE, self.role_prod)
		role.chats = [r for r in role.chats if r.telegram_chat != self.chat_news]
		role.append("chats", {"telegram_chat": self.chat_finance})
		role.save()

		access.reload()
		effective = {r.telegram_chat: r.effective_permission for r in access.chat_permissions}
		self.assertEqual(effective[self.chat_news], "Deny")
		self.assertEqual(effective[self.chat_finance], "Allow")
		self.assertEqual(access.sync_status, "Changes Pending")
		self.assertFalse(self.tg.calls, "Telegram must not change before the administrator applies it")

		self.assertEqual(role.apply_pending_changes(), 1)
		self.assertEqual([b["chat_id"] for b in self.tg.calls_for("banChatMember")], [chat_id(self.chat_news)])
		self.assertEqual({c["chat_id"] for c in self.tg.calls_for("createChatInviteLink")}, {chat_id(self.chat_finance)})
		access.reload()
		self.assertEqual(access.sync_status, "Synced")
		self.assertNotIn(self.chat_news, [r.telegram_chat for r in access.chat_permissions], "cleaned-up rows are pruned")

	def test_auto_apply_role_changes(self):
		self.set_settings(auto_apply_role_changes=1)
		_, access = self.approved_access(self.new_tg_user())
		self.tg.calls.clear()
		role = frappe.get_doc(DT_ROLE, self.role_prod)
		role.append("chats", {"telegram_chat": self.chat_finance})
		role.save()
		self.assertEqual({c["chat_id"] for c in self.tg.calls_for("createChatInviteLink")}, {chat_id(self.chat_finance)})

	def test_deactivating_chat_denies_it(self):
		_, access = self.approved_access(self.new_tg_user())
		chat = frappe.get_doc("Telegram Chat", self.chat_prod)
		chat.is_active = 0
		chat.save()
		self.assertEqual(row_for(access, self.chat_prod).effective_permission, "Deny")


class TestFailuresAndRetries(TAMTestCase):
	def test_backoff_is_bounded(self):
		self.assertEqual([backoff_minutes(n, 2, 60) for n in (1, 2, 3, 4, 5, 6, 7)], [2, 4, 8, 16, 32, 60, 60])
		self.assertEqual(backoff_minutes(1, 2, 60, retry_after_seconds=301), 6)

	def test_transient_failure_is_retried_later(self):
		self.tg.fail["createChatInviteLink"] = [api_error("createChatInviteLink", 429, "Too Many Requests", retry_after=30)]
		req, access = self.approved_access(self.new_tg_user())
		failed = [r for r in frappe.get_doc(DT_ACCESS, access.name).chat_permissions if r.sync_status == "Failed"]
		self.assertEqual(len(failed), 1)
		self.assertTrue(failed[0].retryable)
		self.assertEqual(failed[0].sync_attempts, 1)
		self.assertGreater(get_datetime(failed[0].next_retry_on), now_datetime())
		self.assertEqual(frappe.db.get_value(DT_ACCESS, access.name, "sync_status"), "Partially Synced")

		# Not due yet: the scheduler does nothing.
		self.tg.calls.clear()
		retry_failed_syncs()
		self.assertFalse(self.tg.calls_for("createChatInviteLink"))

		# Due: retried successfully, other chats untouched (idempotent).
		frappe.db.set_value("Employee Telegram Chat Permission", failed[0].name, "next_retry_on", add_to_date(now_datetime(), minutes=-1))
		retry_failed_syncs()
		self.assertEqual(len(self.tg.calls_for("createChatInviteLink")), 1)
		self.assertEqual(frappe.db.get_value(DT_ACCESS, access.name, "sync_status"), "Synced")

	def test_retries_stop_after_max_attempts(self):
		self.set_settings(max_sync_attempts=2)
		self.tg.fail["getChatMember"] = [api_error("getChatMember", 502, "Bad Gateway") for _ in range(10)]
		_, access = self.approved_access(self.new_tg_user(), role="", overrides={self.chat_mgmt: "Allow"})
		row = row_for(access, self.chat_mgmt)
		self.assertEqual((row.sync_attempts, row.retryable), (1, 1))
		frappe.db.set_value(row.doctype, row.name, "next_retry_on", add_to_date(now_datetime(), minutes=-1))
		sync_access(access.name, mode="retry", context="retry")
		row = row_for(access, self.chat_mgmt)
		self.assertEqual(row.sync_attempts, 2)
		self.assertFalse(row.retryable)
		self.assertIsNone(row.next_retry_on)

	def test_permanent_failure_needs_admin(self):
		self.tg.fail["getChatMember"] = [api_error("getChatMember", 400, "Bad Request: chat not found")]
		_, access = self.approved_access(self.new_tg_user(), role="", overrides={self.chat_mgmt: "Allow"})
		row = row_for(access, self.chat_mgmt)
		self.assertEqual(row.sync_status, "Action Required")
		self.assertFalse(row.retryable)
		self.assertEqual(frappe.db.get_value("Telegram Chat", self.chat_mgmt, "bot_status"), "Error")

		# Administrator retry after fixing the chat.
		access.reload()
		access.retry_failed()
		self.assertEqual(row_for(access, self.chat_mgmt).sync_status, "Synced")

	def test_bot_not_admin_error(self):
		self.tg.fail["createChatInviteLink"] = [api_error("createChatInviteLink", 400, "Bad Request: not enough rights to manage chat invite link")]
		_, access = self.approved_access(self.new_tg_user(), role="", overrides={self.chat_mgmt: "Allow"})
		self.assertEqual(row_for(access, self.chat_mgmt).sync_status, "Action Required")

	def test_blocked_bot_marks_notification_failed(self):
		self.tg.fail["sendMessage"] = [api_error("sendMessage", 403, "Forbidden: bot was blocked by the user")]
		req, _ = self.approved_access(self.new_tg_user())
		self.assertEqual(frappe.db.get_value("Telegram Access Request", req.name, "notification_status"), "Failed")


class TestMissingConfiguration(TAMTestCase):
	def test_missing_token_raises_config_error(self):
		from telegram_access_manager.credentials import get_bot_token

		with (
			patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}),
			patch.object(frappe, "conf", frappe._dict()),
			patch("telegram_access_manager.credentials._settings_secret", return_value=None),
		):
			self.assertRaises(TelegramConfigError, get_bot_token)
		with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "not-a-token"}):
			self.assertRaises(TelegramConfigError, get_bot_token)

	def test_sync_without_token_records_failure(self):
		with patch("telegram_access_manager.telegram.get_client", side_effect=TelegramConfigError("not configured")):
			req, access = self.approved_access(self.new_tg_user())
		self.assertEqual(req.status, "Approved")  # the decision is recorded ...
		access.reload()
		self.assertEqual(access.sync_status, "Failed")  # ... but Telegram is not claimed to have changed
		self.assertTrue(all(r.membership_status == "Unknown" for r in access.chat_permissions))
		self.assertEqual(frappe.db.get_value("Telegram Access Request", req.name, "notification_status"), "Failed")

	def test_config_status_never_contains_secrets(self):
		from telegram_access_manager.credentials import get_config_status
		from telegram_access_manager.tests.utils import FAKE_SECRET, FAKE_TOKEN

		status = str(get_config_status())
		self.assertNotIn(FAKE_TOKEN.split(":")[1], status)
		self.assertNotIn(FAKE_SECRET, status)
