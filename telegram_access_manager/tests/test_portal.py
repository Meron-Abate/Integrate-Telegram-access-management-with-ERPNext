import json
import os
from unittest.mock import patch

import frappe

from telegram_access_manager.api import portal
from telegram_access_manager.constants import DT_ACCESS, DT_CHAT, DT_REQUEST, SETTINGS
from telegram_access_manager.services.requests import submit_request_from_telegram
from telegram_access_manager.tests.utils import MANAGER, OUTSIDER, VERIFIER, TAMTestCase


class TestPortalApi(TAMTestCase):
	def new_request(self):
		_, name = submit_request_from_telegram(self.new_tg_user())
		return name

	def test_portal_requires_telegram_roles(self):
		frappe.set_user(OUTSIDER)
		for fn, kwargs in [
			(portal.get_boot, {}),
			(portal.get_requests, {}),
			(portal.get_people, {}),
			(portal.get_chats, {}),
			(portal.search_employees, {}),
		]:
			self.assertRaises(frappe.PermissionError, fn, **kwargs)

	def test_verifier_cannot_use_manager_endpoints(self):
		name = self.new_request()
		frappe.set_user(VERIFIER)
		self.assertTrue(portal.get_boot()["is_manager"] is False)
		self.assertRaises(frappe.PermissionError, portal.get_settings)
		self.assertRaises(frappe.PermissionError, portal.save_settings, values={"enabled": 0})
		self.assertRaises(frappe.PermissionError, portal.chat_action, name=self.chat_prod, action="deactivate")
		self.assertRaises(frappe.PermissionError, portal.save_role, role_name="X", chats=[])
		portal.save_request(name, employee=self.emp_a, role=self.role_prod, access={})
		self.assertRaises(frappe.PermissionError, portal.approve_request, name)

	def test_switches_translate_to_overrides_and_approve(self):
		name = self.new_request()
		frappe.set_user(MANAGER)
		access = {self.chat_prod: True, self.chat_equip: False, self.chat_news: True, self.chat_mgmt: True, self.chat_finance: False}
		payload = portal.save_request(name, employee=self.emp_a, role=self.role_prod, access=json.dumps(access))
		on = {c["chat"] for c in payload["chats"] if c["access"]}
		self.assertEqual(on, {self.chat_prod, self.chat_news, self.chat_mgmt})
		overrides = {r.telegram_chat: r.override for r in frappe.get_doc(DT_REQUEST, name).chat_permissions}
		self.assertEqual(overrides[self.chat_equip], "Deny")  # role chat switched off
		self.assertEqual(overrides[self.chat_mgmt], "Allow")  # extra chat switched on
		self.assertNotIn(self.chat_finance, overrides)  # off and not in role: nothing stored

		result = portal.approve_request(name, notes="ok")
		self.assertEqual(result["status"], "Approved")
		self.assertFalse(result["editable"])
		linked = {c["chat_id"] for c in self.tg.calls_for("createChatInviteLink")}
		self.assertEqual(linked, {frappe.db.get_value(DT_CHAT, c, "telegram_chat_id") for c in on})

	def test_unknown_chat_rejected(self):
		name = self.new_request()
		frappe.set_user(MANAGER)
		self.assertRaises(frappe.ValidationError, portal.save_request, name, employee=self.emp_a, access={"No Such Chat": True})

	def test_closed_request_is_read_only(self):
		name = self.new_request()
		frappe.set_user(MANAGER)
		portal.close_request(name, "reject", "not ours")
		self.assertFalse(portal.get_request(name)["editable"])
		self.assertRaises(frappe.ValidationError, portal.save_request, name, employee=self.emp_a)

	def test_person_view_and_save_applies_changes(self):
		user = self.new_tg_user()
		_, access = self.approved_access(user)
		frappe.set_user(MANAGER)
		person = portal.get_person(access.name)
		self.assertEqual(person["status"], "Up to date")
		membership = {c["chat"]: c["membership"] for c in person["chats"] if c["access"]}
		self.assertEqual(membership[self.chat_prod], "Invite sent")
		self.assertNotIn("sync_attempts", json.dumps(person, default=str), "technical fields stay in the desk")

		self.tg.set_member(frappe.db.get_value(DT_CHAT, self.chat_prod, "telegram_chat_id"), user["id"])
		self.tg.calls.clear()
		switches = {c["chat"]: c["access"] for c in person["chats"]}
		switches[self.chat_prod] = False
		person = portal.save_person(access.name, role=self.role_prod, access=switches)
		self.assertEqual([b["chat_id"] for b in self.tg.calls_for("banChatMember")], [frappe.db.get_value(DT_CHAT, self.chat_prod, "telegram_chat_id")])
		self.assertFalse(next(c for c in person["chats"] if c["chat"] == self.chat_prod)["access"])

	def test_revoke_and_restore_via_portal(self):
		_, access = self.approved_access(self.new_tg_user())
		frappe.set_user(MANAGER)
		person = portal.person_action(access.name, "revoke", reason="left", notify=0)
		self.assertFalse(person["active"])
		self.assertTrue(all(not c["access"] for c in person["chats"]))
		person = portal.person_action(access.name, "restore")
		self.assertTrue(person["active"])

	def test_attention_tab(self):
		_, access = self.approved_access(self.new_tg_user())
		frappe.db.set_value(DT_ACCESS, access.name, {"review_required": 1, "review_reason": "Employee left"})
		frappe.set_user(MANAGER)
		self.assertIn(access.name, [p["name"] for p in portal.get_people("attention")])

	def test_chat_actions(self):
		chat = frappe.get_doc(
			{"doctype": DT_CHAT, "chat_name": "TAM Portal Chat", "telegram_chat_id": "-1007000000001", "chat_type": "Supergroup", "is_active": 0}
		)
		chat.flags.tam_discovered = True
		chat.registration_status = "Pending Review"
		chat.insert()
		frappe.set_user(MANAGER)
		self.assertIn("TAM Portal Chat", [c["name"] for c in portal.get_chats("review")])
		self.assertTrue(portal.chat_action("TAM Portal Chat", "check")["result"]["ok"])
		self.assertEqual(portal.chat_action("TAM Portal Chat", "activate")["chat"]["status"], "Active")
		self.assertEqual(portal.chat_action("TAM Portal Chat", "deactivate")["chat"]["status"], "Inactive")

	def test_roles(self):
		frappe.set_user(MANAGER)
		created = portal.save_role(role_name="TAM Portal Role", chats=json.dumps([self.chat_prod, self.chat_prod, self.chat_news]))
		role = next(r for r in portal.get_roles()["roles"] if r["name"] == created["name"])
		self.assertEqual(role["chats"], [self.chat_prod, self.chat_news])

		_, access = self.approved_access(self.new_tg_user(), role=created["name"])
		frappe.set_user(MANAGER)
		result = portal.save_role(name=created["name"], role_name="", chats=[self.chat_prod], active=1)
		self.assertEqual(result["pending"], 1)
		self.assertEqual(portal.apply_role(created["name"]), 1)

	def test_settings_never_expose_token(self):
		frappe.set_user(MANAGER)
		with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}), patch.object(frappe, "conf", frappe._dict()):
			data = portal.save_settings(values={"invite_link_expiry_hours": 12}, bot_token="555666777:TEST_ONLY_token_entered_in_portal_x")
			self.assertEqual(data["token"], {"configured": True, "source": "Settings"})
		self.assertNotIn("TEST_ONLY", json.dumps(data, default=str))
		self.assertEqual(frappe.db.get_single_value(SETTINGS, "invite_link_expiry_hours"), 12)
		self.assertNotIn("bot_token", data["values"])
