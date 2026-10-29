import re

import frappe

from telegram_access_manager.bot import registration
from telegram_access_manager.bot.handlers import dispatch
from telegram_access_manager.bot.ingest import ingest_update
from telegram_access_manager.constants import DT_ACCESS, DT_AUDIT, DT_CHAT, DT_COMMUNITY, DT_INVITE, DT_ROLE
from telegram_access_manager.services.permissions import compute_for_rows
from telegram_access_manager.tests.utils import TAMTestCase, api_error

GROUP_ID = -1001111111111
CHANNEL_ID = -1002222222222


class RegistrationTestCase(TAMTestCase):
	def setUp(self):
		super().setUp()
		self.set_settings(default_telegram_community=self.community)
		self.admin = self.new_tg_user(first_name="Chat", last_name="Admin")

	# --- update builders --------------------------------------------------------------------
	def group_message(self, text="/register", chat_id=GROUP_ID, title="Ops Group", sender=None, chat_type="supergroup", **extra):
		sender = sender or self.admin
		return {
			"message": {
				"message_id": 10,
				"from": sender,
				"chat": {"id": chat_id, "type": chat_type, "title": title},
				"text": text,
				**extra,
			}
		}

	def channel_post(self, text, chat_id=CHANNEL_ID, title="Company News"):
		return {
			"channel_post": {
				"message_id": 20,
				"sender_chat": {"id": chat_id, "type": "channel", "title": title},
				"chat": {"id": chat_id, "type": "channel", "title": title},
				"text": text,
			}
		}

	def private(self, user, text):
		return {"message": {"message_id": 1, "from": user, "chat": {"id": user["id"], "type": "private"}, "text": text}}

	def make_admin(self, chat_id=GROUP_ID, user=None, status="administrator"):
		self.tg.set_member(chat_id, (user or self.admin)["id"], status)

	def chats(self, chat_id=GROUP_ID):
		return frappe.get_all(DT_CHAT, filters={"telegram_chat_id": str(chat_id)}, fields=["*"])

	def last_reply(self):
		return self.tg.calls_for("sendMessage")[-1]

	def community_label(self, name=None):
		return frappe.db.get_value(DT_COMMUNITY, name or self.community, "community_name")


class TestDefaultCommunitySetting(RegistrationTestCase):
	def test_settings_reference_existing_community(self):
		self.assertEqual(frappe.get_single("Telegram Access Settings").default_telegram_community, self.community)
		meta = frappe.get_meta("Telegram Access Settings").get_field("default_telegram_community")
		self.assertEqual((meta.fieldtype, meta.options), ("Link", "Telegram Community"))

	def test_missing_or_inactive_community_rejected_in_settings(self):
		self.assertRaises(frappe.ValidationError, self.set_settings, default_telegram_community="No Such Community")
		inactive = frappe.get_doc({"doctype": DT_COMMUNITY, "community_name": "TAM Inactive Community", "is_active": 0}).insert()
		self.assertRaises(frappe.ValidationError, self.set_settings, default_telegram_community=inactive.name)

	def test_community_link_and_identifier_stay_optional(self):
		meta = frappe.get_meta(DT_COMMUNITY)
		self.assertFalse(meta.get_field("community_link").reqd)
		self.assertFalse(meta.get_field("telegram_identifier").reqd)


class TestGroupRegistration(RegistrationTestCase):
	def test_admin_registers_group_under_default_community(self):
		self.make_admin()
		self.assertTrue(dispatch(self.group_message()))
		[chat] = self.chats()
		self.assertEqual(chat.community, self.community)
		self.assertEqual(chat.chat_type, "Supergroup")
		self.assertEqual(chat.telegram_title, "Ops Group")
		self.assertEqual(chat.chat_name, "Ops Group")
		self.assertFalse(chat.is_active, "registration never activates a chat")
		self.assertEqual(chat.registration_status, "Pending Review")
		self.assertEqual(chat.registration_source, "/register Command")
		self.assertEqual(chat.registered_by_telegram_user_id, str(self.admin["id"]))
		self.assertEqual(chat.bot_status, "Administrator")
		reply = self.last_reply()
		self.assertEqual(reply["chat_id"], GROUP_ID)
		self.assertIn(f"registered under “{self.community_label()}”", reply["text"])
		self.assertIn("awaiting administrator review", reply["text"])
		self.assertNotIn(chat.name, reply["text"].replace("Ops Group", ""), "no internal record IDs")

	def test_uses_configured_community_record(self):
		other = frappe.get_doc({"doctype": DT_COMMUNITY, "community_name": "TAM Second Community"}).insert()
		self.set_settings(default_telegram_community=other.name)
		self.make_admin()
		dispatch(self.group_message())
		self.assertEqual(self.chats()[0].community, other.name)
		self.assertIn("TAM Second Community", self.last_reply()["text"])

	def test_creator_can_register_and_basic_group_uses_single_use_links(self):
		basic_id = -5332900001
		self.make_admin(basic_id, status="creator")
		dispatch(self.group_message(chat_id=basic_id, chat_type="group", title="Basic Group"))
		[chat] = self.chats(basic_id)
		self.assertEqual((chat.chat_type, chat.access_method), ("Group", "Single-Use Invite Link"))

	def test_reregistration_updates_title_without_duplicates(self):
		self.make_admin()
		dispatch(self.group_message())
		dispatch(self.group_message(title="Operations Team"))
		[chat] = self.chats()
		self.assertEqual(chat.telegram_title, "Operations Team")
		self.assertEqual(chat.chat_name, "Ops Group", "the ERPNext name chosen at registration is kept")
		self.assertIn("still awaiting administrator review", self.last_reply()["text"])

	def test_already_registered_active_chat_is_not_changed(self):
		self.make_admin(-1009000000001)
		before = frappe.get_doc(DT_CHAT, self.chat_prod)
		dispatch(self.group_message(chat_id=-1009000000001, title="Renamed Production"))
		after = frappe.get_doc(DT_CHAT, self.chat_prod)
		self.assertTrue(after.is_active)
		self.assertEqual((after.registration_status, after.access_method, after.community), (before.registration_status, before.access_method, before.community))
		self.assertEqual(after.telegram_title, "Renamed Production")
		self.assertIn("already registered", self.last_reply()["text"])

	def test_existing_chat_without_community_is_associated(self):
		chat = frappe.get_doc({"doctype": DT_CHAT, "chat_name": "TAM Loose Chat", "telegram_chat_id": str(GROUP_ID), "chat_type": "Supergroup"}).insert()
		self.make_admin()
		dispatch(self.group_message())
		self.assertEqual(frappe.db.get_value(DT_CHAT, chat.name, "community"), self.community)

	def test_ordinary_member_cannot_register(self):
		self.make_admin(status="member")
		dispatch(self.group_message())
		self.assertFalse(self.chats())
		self.assertEqual(self.last_reply()["text"], registration.MSG_NOT_AUTHORIZED)
		self.assertTrue(frappe.db.exists(DT_AUDIT, {"event_type": "Chat Registration Refused", "telegram_user_id": str(self.admin["id"])}))

	def test_anonymous_admin_and_foreign_sender_chat_fail_closed(self):
		anon = {"id": 1087968824, "is_bot": True, "first_name": "Group"}
		dispatch(self.group_message(sender=anon, sender_chat={"id": GROUP_ID, "type": "supergroup"}))
		self.assertEqual(self.last_reply()["text"], registration.MSG_ANONYMOUS)
		dispatch(self.group_message(sender=anon, sender_chat={"id": -1003333333333, "type": "channel"}))
		self.assertEqual(self.last_reply()["text"], registration.MSG_NOT_AUTHORIZED)
		self.assertFalse(self.chats())

	def test_missing_sender_fails_closed(self):
		update = self.group_message()
		update["message"].pop("from")
		dispatch(update)
		self.assertFalse(self.chats())

	def test_missing_or_inactive_default_community_fails_safely(self):
		self.make_admin()
		self.set_settings(default_telegram_community=None)
		dispatch(self.group_message())
		self.assertEqual(self.last_reply()["text"], registration.MSG_NOT_CONFIGURED)
		self.set_settings(default_telegram_community=self.community)
		frappe.db.set_value(DT_COMMUNITY, self.community, "is_active", 0)
		dispatch(self.group_message())
		self.assertEqual(self.last_reply()["text"], registration.MSG_NOT_CONFIGURED)
		self.assertFalse(self.chats())

	def test_invalid_chat_metadata_fails_safely(self):
		for bad in ({"id": "not-a-number", "type": "supergroup"}, {"id": -100123, "type": "secret"}, {"type": "group"}, {"id": True, "type": "group"}):
			with self.assertRaises(registration.RegistrationError):
				registration.chat_metadata(bad)
		self.make_admin()
		dispatch({"message": {"from": self.admin, "chat": {"id": "x", "type": "supergroup"}, "text": "/register"}})
		self.assertEqual(frappe.db.count(DT_CHAT, {"telegram_chat_id": "x"}), 0)

	def test_chat_of_another_community_is_not_reassigned(self):
		other = frappe.get_doc({"doctype": DT_COMMUNITY, "community_name": "TAM Other Community"}).insert()
		frappe.get_doc(
			{"doctype": DT_CHAT, "chat_name": "TAM Other Chat", "telegram_chat_id": str(GROUP_ID), "chat_type": "Supergroup", "community": other.name}
		).insert()
		self.make_admin()
		dispatch(self.group_message(title="Hijack attempt"))
		[chat] = self.chats()
		self.assertEqual(chat.community, other.name)
		self.assertNotEqual(chat.telegram_title, "Hijack attempt")
		self.assertEqual(self.last_reply()["text"], registration.MSG_OTHER_COMMUNITY)

	def test_rejected_chat_cannot_reregister(self):
		self.make_admin()
		dispatch(self.group_message())
		chat = frappe.get_doc(DT_CHAT, self.chats()[0].name)
		chat.reject_registration("Not a company chat")
		dispatch(self.group_message())
		self.assertEqual(self.last_reply()["text"], registration.MSG_REJECTED)
		self.assertEqual(frappe.db.get_value(DT_CHAT, chat.name, "registration_status"), "Rejected")

	def test_arguments_cannot_target_another_chat(self):
		self.make_admin()
		dispatch(self.group_message(text="/register -1009999999999"))
		self.assertFalse(self.chats(-1009999999999))
		self.assertEqual(len(self.chats()), 1)
		self.assertIn("Arguments are ignored", self.last_reply()["text"])

	def test_bot_mentions(self):
		self.make_admin()
		self.assertFalse(dispatch(self.group_message(text="/register@SomeOtherBot")))
		self.assertFalse(self.chats())
		self.assertTrue(dispatch(self.group_message(text="/register@tam_test_bot")))
		self.assertEqual(len(self.chats()), 1)

	def test_ordinary_group_messages_are_ignored(self):
		self.assertFalse(dispatch(self.group_message(text="hello everyone")))
		self.assertFalse(dispatch(self.group_message(text="/start")))
		self.assertFalse(self.tg.calls_for("sendMessage"))

	def test_bot_without_rights_is_refused(self):
		self.make_admin()
		self.tg.bot_member = {"status": "member"}
		dispatch(self.group_message())
		self.assertEqual(self.last_reply()["text"], registration.MSG_BOT_NOT_ADMIN)
		self.tg.bot_member = {"status": "administrator", "can_invite_users": False}
		dispatch(self.group_message())
		self.assertFalse(self.chats())

	def test_missing_ban_right_registers_with_warning(self):
		self.make_admin()
		self.tg.bot_member = {"status": "administrator", "can_invite_users": True, "can_restrict_members": False}
		dispatch(self.group_message())
		self.assertEqual(len(self.chats()), 1)
		self.assertIn("Ban users", self.last_reply()["text"])

	def test_telegram_failure_is_not_reported_as_success(self):
		self.tg.fail["getChatMember"] = [api_error("getChatMember", 502, "Bad Gateway")]
		dispatch(self.group_message())
		self.assertFalse(self.chats())
		self.assertEqual(self.last_reply()["text"], registration.MSG_TELEGRAM_ERROR)
		self.assertNotIn("Bad Gateway", self.last_reply()["text"], "internal errors are not exposed")

	def test_failed_reply_keeps_registration(self):
		self.make_admin()
		self.tg.fail["sendMessage"] = [api_error("sendMessage", 400, "Bad Request: not enough rights to send text messages")]
		self.assertTrue(dispatch(self.group_message()))
		self.assertEqual(len(self.chats()), 1)

	def test_duplicate_updates_create_one_record_and_one_reply(self):
		self.make_admin()
		update = {"update_id": abs(GROUP_ID) % 10**9 + self.admin["id"], **self.group_message()}
		self.assertEqual(ingest_update(update, enqueue=False), "accepted")
		self.assertEqual(ingest_update(update, enqueue=False), "duplicate")
		self.assertEqual(len(self.chats()), 1)
		self.assertEqual(len(self.tg.calls_for("sendMessage")), 1)


class TestRegistrationDoesNotGrantAccess(RegistrationTestCase):
	def test_no_access_side_effects(self):
		user = self.new_tg_user()
		_, access = self.approved_access(user)
		before_rows = {(r.telegram_chat, r.effective_permission) for r in access.chat_permissions}
		self.tg.calls.clear()

		self.make_admin()
		dispatch(self.group_message())
		chat_name = self.chats()[0].name

		access.reload()
		self.assertEqual({(r.telegram_chat, r.effective_permission) for r in access.chat_permissions}, before_rows)
		self.assertFalse(self.tg.calls_for("createChatInviteLink"))
		self.assertFalse(frappe.db.exists(DT_INVITE, {"telegram_chat": chat_name}))

		# Even if placed in a role or allowed individually, a pending (inactive) chat is never granted.
		role = frappe.get_doc(DT_ROLE, self.role_prod)
		role.append("chats", {"telegram_chat": chat_name})
		role.save()
		resolved = compute_for_rows(self.role_prod, [frappe._dict(telegram_chat=chat_name, override="Allow")])
		self.assertEqual(resolved[chat_name]["effective"], "Deny")

		# Join requests for the pending chat are not approved.
		dispatch({"chat_join_request": {"chat": {"id": GROUP_ID, "type": "supergroup"}, "from": user, "date": 0}})
		self.assertFalse(self.tg.calls_for("approveChatJoinRequest"))

	def test_activated_chat_works_with_roles(self):
		self.make_admin()
		dispatch(self.group_message())
		chat = frappe.get_doc(DT_CHAT, self.chats()[0].name)
		chat.is_active = 1
		chat.save()
		self.assertEqual(chat.registration_status, "Registered")
		resolved = compute_for_rows(None, [frappe._dict(telegram_chat=chat.name, override="Allow")])
		self.assertEqual(resolved[chat.name]["effective"], "Allow")


class TestChannelRegistration(RegistrationTestCase):
	def get_code(self, user):
		dispatch(self.private(user, "/register"))
		return re.search(r"/register ([A-Z0-9]{8})", self.last_reply()["text"]).group(1)

	def test_plain_register_in_channel_fails_closed(self):
		dispatch(self.channel_post("/register"))
		self.assertFalse(self.chats(CHANNEL_ID))
		self.assertEqual(self.last_reply()["chat_id"], CHANNEL_ID)
		self.assertIn("one-time code", self.last_reply()["text"])

	def test_signature_alone_is_not_trusted(self):
		post = self.channel_post("/register")
		post["channel_post"]["author_signature"] = "Chat Admin"
		dispatch(post)
		self.assertFalse(self.chats(CHANNEL_ID))

	def test_code_handshake_registers_channel(self):
		code = self.get_code(self.admin)
		self.make_admin(CHANNEL_ID)
		dispatch(self.channel_post(f"/register {code}"))
		[chat] = self.chats(CHANNEL_ID)
		self.assertEqual((chat.chat_type, chat.community, chat.is_active), ("Channel", self.community, 0))
		self.assertEqual(chat.registered_by_telegram_user_id, str(self.admin["id"]))
		reply = self.last_reply()
		self.assertEqual(reply["chat_id"], self.admin["id"], "confirmation goes privately to the verified admin")
		self.assertIn("awaiting administrator review", reply["text"])
		self.assertTrue(self.tg.calls_for("deleteMessage"))

	def test_code_is_single_use(self):
		code = self.get_code(self.admin)
		self.make_admin(CHANNEL_ID)
		dispatch(self.channel_post(f"/register {code}"))
		dispatch(self.channel_post(f"/register {code}", chat_id=-1004444444444, title="Other"))
		self.assertFalse(self.chats(-1004444444444))
		self.assertEqual(self.last_reply()["text"], registration.MSG_CODE_INVALID)

	def test_new_code_replaces_old_one(self):
		old = self.get_code(self.admin)
		new = self.get_code(self.admin)
		self.make_admin(CHANNEL_ID)
		dispatch(self.channel_post(f"/register {old}"))
		self.assertFalse(self.chats(CHANNEL_ID))
		dispatch(self.channel_post(f"/register {new}"))
		self.assertEqual(len(self.chats(CHANNEL_ID)), 1)

	def test_code_of_non_admin_is_refused(self):
		subscriber = self.new_tg_user()
		code = self.get_code(subscriber)
		self.tg.set_member(CHANNEL_ID, subscriber["id"], "member")
		dispatch(self.channel_post(f"/register {code}"))
		self.assertFalse(self.chats(CHANNEL_ID))
		self.assertEqual(self.last_reply()["chat_id"], subscriber["id"])
		self.assertEqual(self.last_reply()["text"], registration.MSG_NOT_AUTHORIZED)

	def test_invalid_code_format_refused(self):
		dispatch(self.channel_post("/register ../../etc"))
		dispatch(self.channel_post("/register -1009000000001"))
		self.assertFalse(self.chats(CHANNEL_ID))
		self.assertFalse(frappe.db.exists(DT_CHAT, {"telegram_chat_id": "-1009000000001", "registration_source": "/register Command"}))


class TestBotAddedDiscovery(RegistrationTestCase):
	def test_discovered_chat_uses_default_community(self):
		dispatch(
			{
				"my_chat_member": {
					"chat": {"id": -1005555555555, "type": "channel", "title": "Discovered Channel"},
					"from": self.admin,
					"date": 0,
					"old_chat_member": {"status": "left"},
					"new_chat_member": {"status": "administrator", "can_invite_users": True},
				}
			}
		)
		[chat] = self.chats(-1005555555555)
		self.assertEqual((chat.community, chat.is_active, chat.registration_status, chat.registration_source), (self.community, 0, "Discovered", "Bot Added"))

