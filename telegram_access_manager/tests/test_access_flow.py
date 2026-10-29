import frappe

from telegram_access_manager.constants import DT_ACCESS, DT_AUDIT, DT_INVITE, DT_REQUEST
from telegram_access_manager.services.requests import submit_request_from_telegram
from telegram_access_manager.tests.utils import MANAGER, OUTSIDER, VERIFIER, TAMTestCase


class TestRequestCreation(TAMTestCase):
	def test_request_created_from_telegram_identity(self):
		user = self.new_tg_user(language_code="en")
		outcome, name = submit_request_from_telegram(user)
		self.assertEqual(outcome, "created")
		req = frappe.get_doc(DT_REQUEST, name)
		self.assertEqual(req.telegram_user_id, str(user["id"]))
		self.assertEqual(req.telegram_username, user["username"])
		self.assertEqual(req.telegram_display_name, "Test User")
		self.assertEqual(req.status, "Pending Verification")
		self.assertEqual(req.verification_status, "Not Verified")
		self.assertIsNone(req.employee)
		self.assertTrue(req.request_timestamp)

	def test_repeated_requests_are_idempotent(self):
		user = self.new_tg_user()
		_, first = submit_request_from_telegram(user)
		outcome, second = submit_request_from_telegram(user)
		self.assertEqual(outcome, "existing")
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count(DT_REQUEST, {"telegram_user_id": str(user["id"])}), 1)

	def test_username_change_updates_open_request(self):
		user = self.new_tg_user()
		_, name = submit_request_from_telegram(user)
		submit_request_from_telegram({**user, "username": "renamed_user"})
		self.assertEqual(frappe.db.get_value(DT_REQUEST, name, "telegram_username"), "renamed_user")

	def test_missing_username_is_accepted(self):
		user = self.new_tg_user()
		user.pop("username")
		outcome, name = submit_request_from_telegram(user)
		self.assertEqual(outcome, "created")
		self.assertIsNone(frappe.db.get_value(DT_REQUEST, name, "telegram_username"))

	def test_daily_request_limit(self):
		self.set_settings(max_requests_per_user_per_day=1)
		user = self.new_tg_user()
		_, name = submit_request_from_telegram(user)
		frappe.get_doc(DT_REQUEST, name).reject("not ours")
		outcome, _ = submit_request_from_telegram(user)
		self.assertEqual(outcome, "rate_limited")

	def test_invalid_user_id_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			submit_request_from_telegram({"id": "-5", "first_name": "x"})

	def test_active_access_does_not_create_new_request(self):
		user = self.new_tg_user()
		self.approved_access(user)
		outcome, _ = submit_request_from_telegram(user)
		self.assertEqual(outcome, "already_active")


class TestVerificationAndApproval(TAMTestCase):
	def _pending(self, employee=None, role=None):
		_, name = submit_request_from_telegram(self.new_tg_user())
		req = frappe.get_doc(DT_REQUEST, name)
		req.employee = employee or self.emp_a
		req.access_role = role if role is not None else self.role_prod
		req.save()
		return req

	def test_role_chats_are_populated_on_save(self):
		req = self._pending()
		chats = {r.telegram_chat: (r.source, r.effective_permission) for r in req.chat_permissions}
		self.assertEqual(
			chats,
			{
				self.chat_prod: ("Role", "Allow"),
				self.chat_equip: ("Role", "Allow"),
				self.chat_news: ("Role", "Allow"),
			},
		)

	def test_client_cannot_forge_effective_permission(self):
		req = self._pending()
		req.append("chat_permissions", {"telegram_chat": self.chat_finance, "override": "Inherit", "effective_permission": "Allow", "source": "Role"})
		req.save()
		allowed = {r.telegram_chat for r in req.chat_permissions if r.effective_permission == "Allow"}
		self.assertNotIn(self.chat_finance, allowed, "an Inherit row outside the role must not be granted")

	def test_protected_fields_cannot_be_edited(self):
		req = self._pending()
		req.status = "Approved"
		self.assertRaises(frappe.PermissionError, req.save)

	def test_manual_request_starts_unverified(self):
		req = frappe.get_doc(
			{"doctype": DT_REQUEST, "telegram_user_id": "123456", "status": "Approved", "verification_status": "Verified"}
		).insert()
		self.assertEqual(req.status, "Pending Verification")
		self.assertEqual(req.verification_status, "Not Verified")
		self.assertEqual(req.request_source, "Manual")

	def test_cannot_approve_without_linked_employee(self):
		_, name = submit_request_from_telegram(self.new_tg_user())
		req = frappe.get_doc(DT_REQUEST, name)
		req.access_role = self.role_prod
		req.save()
		self.assertRaises(frappe.ValidationError, req.approve)
		self.assertFalse(frappe.db.exists(DT_ACCESS, {"telegram_user_id": req.telegram_user_id}))

	def test_manager_approves_directly_after_linking_employee(self):
		req = self._pending()
		self.assertEqual(req.status, "Pending Verification")
		frappe.set_user(MANAGER)
		req = frappe.get_doc(DT_REQUEST, req.name)
		result = req.approve()
		req.reload()
		self.assertEqual(req.status, "Approved")
		self.assertEqual((req.verification_status, req.verified_by), ("Verified", MANAGER))
		self.assertEqual(req.verification_method, "Employee linked in ERPNext")
		self.assertTrue(result["employee_telegram_access"])

	def test_verification_requires_employee(self):
		_, name = submit_request_from_telegram(self.new_tg_user())
		req = frappe.get_doc(DT_REQUEST, name)
		self.assertRaises(frappe.ValidationError, req.verify_identity)

	def test_verifier_can_verify_but_not_approve(self):
		req = self._pending()
		frappe.set_user(VERIFIER)
		req = frappe.get_doc(DT_REQUEST, req.name)
		req.verify_identity()
		self.assertEqual(req.status, "Pending Approval")
		self.assertEqual(req.verified_by, VERIFIER)
		self.assertRaises(frappe.PermissionError, req.approve)

	def test_unauthorized_user_cannot_verify_or_approve(self):
		req = self._pending()
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, req.verify_identity)
		self.assertRaises(frappe.PermissionError, req.approve)
		self.assertRaises(frappe.PermissionError, req.reject, "x")
		self.assertRaises(frappe.PermissionError, req.get_effective_permissions_preview)
		self.assertFalse(frappe.has_permission(DT_REQUEST, "read", user=OUTSIDER))
		self.assertFalse(frappe.has_permission(DT_INVITE, "read", user=OUTSIDER))
		self.assertFalse(frappe.has_permission("Telegram Access Role", "write", user=OUTSIDER))
		self.assertFalse(frappe.has_permission(DT_INVITE, "read", user=VERIFIER))

	def test_separate_verifier_rule(self):
		self.set_settings(require_separate_verifier=1)
		req = self._pending()
		frappe.set_user(MANAGER)
		self.assertRaises(frappe.PermissionError, frappe.get_doc(DT_REQUEST, req.name).approve)  # unverified
		req = frappe.get_doc(DT_REQUEST, req.name)
		req.verify_identity()
		self.assertRaises(frappe.PermissionError, req.approve)

	def test_approval_grants_only_effective_chats(self):
		user = self.new_tg_user()
		req, access = self.approved_access(
			user, overrides={self.chat_equip: "Deny", self.chat_mgmt: "Allow"}
		)
		self.assertEqual(req.status, "Approved")
		self.assertEqual(access.employee, self.emp_a)
		self.assertEqual(access.telegram_user_id, str(user["id"]))
		effective = {r.telegram_chat: r.effective_permission for r in access.chat_permissions}
		self.assertEqual(effective[self.chat_prod], "Allow")
		self.assertEqual(effective[self.chat_equip], "Deny")
		self.assertEqual(effective[self.chat_news], "Allow")
		self.assertEqual(effective[self.chat_mgmt], "Allow")
		self.assertNotIn(self.chat_finance, effective)

		# Telegram: invite links only for the allowed chats, never for Equipment or Finance.
		linked = {c["chat_id"] for c in self.tg.calls_for("createChatInviteLink")}
		chat_ids = lambda *names: {frappe.db.get_value("Telegram Chat", n, "telegram_chat_id") for n in names}  # noqa: E731
		self.assertEqual(linked, chat_ids(self.chat_prod, self.chat_news, self.chat_mgmt))
		self.assertTrue(all(c["creates_join_request"] for c in self.tg.calls_for("createChatInviteLink")))

		# One private message containing the links; links stored encrypted.
		messages = self.tg.calls_for("sendMessage")
		self.assertEqual(len(messages), 1)
		self.assertEqual(messages[0]["chat_id"], user["id"])
		self.assertIn("https://t.me/+FAKE", messages[0]["text"])
		self.assertEqual(frappe.db.get_value(DT_REQUEST, req.name, "notification_status"), "Sent")
		stored = frappe.get_all(DT_INVITE, filters={"employee_telegram_access": access.name}, pluck="invite_link")
		self.assertTrue(stored and all("t.me" not in (v or "") for v in stored))

		# Audit trail must not contain invite links.
		details = " ".join(filter(None, frappe.get_all(DT_AUDIT, filters={"employee_telegram_access": access.name}, pluck="details")))
		self.assertNotIn("t.me/+", details)

	def test_approval_with_no_effective_chat_is_refused(self):
		req = self._pending(role="")
		req.append("chat_permissions", {"telegram_chat": self.chat_prod, "override": "Deny"})
		req.save()
		req.verify_identity()
		self.assertRaises(frappe.ValidationError, req.approve)

	def test_identity_cannot_be_linked_to_two_employees(self):
		user = self.new_tg_user()
		_, access = self.approved_access(user, employee=self.emp_a)
		access.revoke_access("test")
		_, name = submit_request_from_telegram(user)
		req = frappe.get_doc(DT_REQUEST, name)
		req.employee = self.emp_b
		req.access_role = self.role_prod
		req.save()
		self.assertRaises(frappe.ValidationError, req.verify_identity)

	def test_employee_with_active_identity_cannot_get_second(self):
		self.approved_access(self.new_tg_user(), employee=self.emp_a)
		req = self._pending(employee=self.emp_a)
		self.assertRaises(frappe.ValidationError, req.verify_identity)

	def test_changing_employee_resets_verification(self):
		req = self._pending()
		req.verify_identity()
		req.reload()
		req.employee = self.emp_b
		req.save()
		self.assertEqual(req.verification_status, "Not Verified")
		self.assertEqual(req.status, "Pending Verification")

	def test_reject_and_cancel(self):
		req = self._pending()
		req.reject("Could not verify")
		req.reload()
		self.assertEqual(req.status, "Rejected")
		self.assertEqual(req.verification_status, "Failed")
		self.assertEqual(req.notification_status, "Sent")
		self.assertFalse(frappe.db.exists(DT_ACCESS, {"telegram_user_id": req.telegram_user_id}))
		self.assertFalse(self.tg.calls_for("createChatInviteLink"))
		self.assertRaises(frappe.ValidationError, req.approve)

		req2 = self._pending()
		req2.cancel_request("Duplicate")
		self.assertEqual(frappe.db.get_value(DT_REQUEST, req2.name, "status"), "Cancelled")

	def test_closed_request_cannot_be_modified(self):
		req = self._pending()
		req.reject("no")
		req.reload()
		req.access_role = ""
		self.assertRaises(frappe.ValidationError, req.save)

	def test_inactive_employee_cannot_be_approved(self):
		req = self._pending()
		req.verify_identity()
		frappe.db.set_value("Employee", self.emp_a, "status", "Inactive")
		req.reload()
		self.assertRaises(frappe.ValidationError, req.approve)


class TestAddingIndividualChats(TAMTestCase):
	def test_blank_row_survives_live_refresh_and_extra_chat_is_granted(self):
		_, name = submit_request_from_telegram(self.new_tg_user())
		req = frappe.get_doc(DT_REQUEST, name)
		req.employee = self.emp_a
		req.access_role = self.role_prod
		req.save()
		req.append("chat_permissions", {"override": "Allow"})  # "Add row", no chat picked yet
		req.refresh_permissions()
		self.assertEqual(len([r for r in req.chat_permissions if not r.telegram_chat]), 1)

		next(r for r in req.chat_permissions if not r.telegram_chat).telegram_chat = self.chat_mgmt
		req.refresh_permissions()
		req.save()
		effective = {r.telegram_chat: r.effective_permission for r in req.chat_permissions}
		self.assertEqual(effective[self.chat_mgmt], "Allow")
		self.assertEqual(effective[self.chat_prod], "Allow")
		self.assertNotIn(self.chat_finance, effective)
