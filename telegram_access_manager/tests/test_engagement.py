from datetime import date, datetime, timedelta
from unittest.mock import patch

import frappe

from telegram_access_manager.bot.handlers import dispatch
from telegram_access_manager.bot.ingest import ingest_update
from telegram_access_manager.constants import DT_ACTIVITY, DT_ENGAGEMENT_LOG
from telegram_access_manager.services import engagement
from telegram_access_manager.tests.utils import TAMTestCase, api_error

TZ = engagement.TIMEZONE
MONDAY = date(2026, 11, 9)


def at(day: date, hour=10, minute=0) -> datetime:
	return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def day(n: int) -> date:
	return MONDAY + timedelta(days=n)


class EngagementTestCase(TAMTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.chat_motion = cls.make_chat("TAM Motion Group", "-1009100000001")
		cls.chat_other = cls.make_chat("TAM Print Group", "-1009100000002")
		cls.chat_shared = cls.make_chat("TAM Creative Group", "-1009100000003")
		cls.chat_chan = cls.make_chat("TAM Motion Channel", "-1009100000004", "Channel")
		cls.team = cls.make_role("TAM Motion Team", [cls.chat_motion, cls.chat_shared, cls.chat_chan])
		cls.head = cls.make_role("TAM Motion Head", [cls.chat_motion, cls.chat_shared, cls.chat_other])
		cls.hr = cls.make_role("TAM HR", [cls.chat_motion])
		cls.print_team = cls.make_role("TAM Print Team", [cls.chat_other, cls.chat_shared])
		cls.employees = [
			cls.make_employee(n, "Eng")
			for n in ("Almaz", "Bekele", "Chaltu", "Dawit", "Eden", "Fikru", "Genet")
		]

	def setUp(self):
		super().setUp()
		self.set_settings(
			engagement_enabled=1,
			engagement_excluded_roles="TAM HR\nManagers\nAdmin",
			engagement_participation_target=50,
		)

	# --- helpers
	def member(self, index, role=None, overrides=None, **user):
		tg_user = self.new_tg_user(**user)
		_req, access = self.approved_access(
			tg_user, employee=self.employees[index], role=role or self.team, overrides=overrides
		)
		return tg_user, access

	def msg(self, chat, user, message_id, when=None, **extra):
		chat_id = frappe.db.get_value("Telegram Chat", chat, "telegram_chat_id")
		when = when or at(MONDAY)
		message = {
			"message_id": message_id,
			"from": user,
			"chat": {"id": int(chat_id), "type": "supergroup"},
			"date": int(when.timestamp()),
			"text": "status update",
			**extra,
		}
		return engagement.record_message({k: v for k, v in message.items() if v is not None})

	def motion(self):
		return engagement.find_departments(self.chat_motion)[0]

	def sent_texts(self):
		return self.tg.calls_for("sendMessage")


class TestRoleMatching(EngagementTestCase):
	def test_team_and_head_sharing_a_chat_form_a_department(self):
		dept = self.motion()
		self.assertEqual(dept["name"], "TAM Motion Team")
		self.assertEqual(dept["head_roles"], ["TAM Motion Head"])
		self.assertEqual(dept["chat_id"], "-1009100000001")

	def test_shared_chats_and_channels_are_not_department_groups(self):
		chats = {d["chat"] for d in engagement.find_departments()}
		self.assertIn(self.chat_motion, chats)
		self.assertIn(self.chat_other, chats)  # print team's own group
		self.assertNotIn(self.chat_shared, chats)  # two teams share it
		self.assertNotIn(self.chat_chan, chats)  # channel
		self.assertNotIn(self.chat_news, chats)

	def test_head_of_another_department_is_not_matched(self):
		other = next(d for d in engagement.find_departments() if d["chat"] == self.chat_other)
		self.assertEqual(
			other["head_roles"], []
		)  # "TAM Motion Head" shares the chat but is not "TAM Print Head"

	def test_inactive_chat_is_not_a_department(self):
		frappe.db.set_value("Telegram Chat", self.chat_motion, "is_active", 0)
		self.assertEqual(engagement.find_departments(self.chat_motion), [])

	def test_role_classification(self):
		self.assertEqual(engagement.classify_role("Motion Team"), ("team", "motion"))
		self.assertEqual(engagement.classify_role("Print Works Head"), ("head", "print works"))
		self.assertEqual(engagement.classify_role("Creative Director"), (None, ""))
		self.assertEqual(engagement.classify_role("HR"), (None, ""))


class TestEligibility(EngagementTestCase):
	def test_only_department_roles_are_eligible_and_excluded_roles_are_left_out(self):
		_, member = self.member(0)
		_, head = self.member(1, role=self.head)
		_, hr = self.member(2, role=self.hr)  # has Telegram access, but is excluded from targets
		eligible = {m.access for m in engagement.get_members(self.motion())}
		self.assertEqual(eligible, {member.name, head.name})
		self.assertTrue(
			frappe.db.get_value("Employee Telegram Access", hr.name, "is_active")
		)  # access untouched

	def test_denied_or_revoked_people_are_not_eligible(self):
		_, denied = self.member(0, overrides={self.chat_motion: "Deny"})
		_, revoked = self.member(1)
		revoked.revoke_access("test")
		self.assertEqual(engagement.get_members(self.motion()), [])
		self.assertTrue(denied.name)

	def test_people_who_left_the_chat_are_not_eligible(self):
		_, access = self.member(0)
		frappe.db.set_value(
			"Employee Telegram Chat Permission",
			{"parent": access.name, "telegram_chat": self.chat_motion},
			"membership_status",
			"Left",
		)
		self.assertEqual(engagement.get_members(self.motion()), [])

	def test_excluded_roles_setting_is_configurable(self):
		_, member = self.member(0)
		self.member(1, role=self.head)
		self.set_settings(engagement_excluded_roles="TAM Motion Head")
		self.assertEqual([m.access for m in engagement.get_members(self.motion())], [member.name])


class TestRecording(EngagementTestCase):
	def test_message_is_recorded_without_content(self):
		user, _ = self.member(0)
		self.assertEqual(self.msg(self.chat_motion, user, 11), "recorded")
		row = frappe.get_doc(DT_ACTIVITY, "-1009100000001:11")
		self.assertEqual(
			(row.telegram_user_id, row.message_type, row.message_id), (str(user["id"]), "text", 11)
		)
		self.assertNotIn("status update", str(row.as_dict()))

	def test_duplicate_message_ids_are_counted_once(self):
		user, _ = self.member(0)
		self.assertEqual(self.msg(self.chat_motion, user, 12), "recorded")
		self.assertEqual(self.msg(self.chat_motion, user, 12), "duplicate")
		self.assertEqual(frappe.db.count(DT_ACTIVITY, {"telegram_user_id": str(user["id"])}), 1)

	def test_same_message_id_in_another_chat_is_a_different_message(self):
		user, _ = self.member(0)
		self.assertEqual(self.msg(self.chat_motion, user, 5), "recorded")
		self.assertEqual(self.msg(self.chat_other, user, 5), "recorded")  # same number, different chat
		self.assertEqual(
			frappe.db.count(DT_ACTIVITY, {"message_id": 5, "telegram_user_id": str(user["id"])}), 2
		)

	def test_bots_service_messages_and_anonymous_senders_are_ignored(self):
		user, _ = self.member(0)
		bot = self.new_tg_user(is_bot=True)
		self.assertEqual(self.msg(self.chat_motion, bot, 20), "ignored")
		self.assertEqual(
			self.msg(self.chat_motion, user, 21, new_chat_members=[{"id": 1}], text=None), "ignored"
		)
		service = {
			"message_id": 22,
			"from": user,
			"chat": {"id": -1009100000001, "type": "supergroup"},
			"date": 1,
			"pinned_message": {},
		}
		self.assertEqual(engagement.record_message(service), "ignored")
		self.assertEqual(self.msg(self.chat_motion, user, 23, sender_chat={"id": -1009100000001}), "ignored")
		self.assertEqual(frappe.db.count(DT_ACTIVITY, {"telegram_chat_id": "-1009100000001"}), 0)

	def test_unmatched_telegram_accounts_are_not_recorded(self):
		stranger = self.new_tg_user()
		self.assertEqual(self.msg(self.chat_motion, stranger, 30), "ignored")
		self.assertFalse(frappe.db.exists(DT_ACTIVITY, "-1009100000001:30"))

	def test_non_department_chats_are_not_recorded(self):
		user, _ = self.member(0)
		self.assertEqual(self.msg(self.chat_shared, user, 31), "ignored")
		self.assertEqual(self.msg(self.chat_chan, user, 32), "ignored")

	def test_nothing_is_recorded_while_the_feature_is_off(self):
		user, _ = self.member(0)
		self.set_settings(engagement_enabled=0)
		self.assertEqual(self.msg(self.chat_motion, user, 33), "ignored")

	def test_media_counts_and_topic_ids_are_kept(self):
		user, _ = self.member(0)
		self.msg(
			self.chat_motion, user, 40, text=None, photo=[{}], message_thread_id=7, is_topic_message=True
		)
		self.msg(self.chat_motion, user, 41, message_thread_id=40)  # a reply in a normal group, not a topic
		topic, reply = (frappe.get_doc(DT_ACTIVITY, f"-1009100000001:{i}") for i in (40, 41))
		self.assertEqual((topic.message_type, topic.message_thread_id), ("photo", 7))
		self.assertFalse(reply.message_thread_id)

	def test_messages_are_bucketed_by_addis_ababa_date(self):
		user, _ = self.member(0)
		# Sunday 22:30 UTC is already Monday 01:30 in Addis Ababa.
		utc = datetime(2026, 11, 8, 22, 30, tzinfo=engagement.ZoneInfo("UTC"))
		self.msg(self.chat_motion, user, 50, when=utc)
		self.assertEqual(frappe.db.get_value(DT_ACTIVITY, "-1009100000001:50", "activity_date"), MONDAY)

	def test_webhook_group_messages_are_counted_and_registration_still_works(self):
		user, _ = self.member(0)
		update = {
			"update_id": user["id"],
			"message": {
				"message_id": 60,
				"from": user,
				"chat": {"id": -1009100000001, "type": "supergroup"},
				"date": int(at(MONDAY).timestamp()),
				"text": "hello",
			},
		}
		self.assertEqual(ingest_update(update, enqueue=False), "accepted")
		self.assertEqual(ingest_update(update, enqueue=False), "duplicate")
		self.assertEqual(frappe.db.count(DT_ACTIVITY, {"telegram_user_id": str(user["id"])}), 1)
		# A /register command in the same chat is still routed to the registration handler.
		with (
			patch(
				"telegram_access_manager.bot.handlers.registration.parse_command", return_value=(True, "")
			) as parse,
			patch(
				"telegram_access_manager.bot.handlers.registration.handle_group_register", return_value="ok"
			),
		):
			message = {
				"message_id": 61,
				"from": user,
				"chat": {"id": -1009100000001, "type": "supergroup"},
				"date": 1,
				"text": "/register",
			}
			self.assertTrue(dispatch({"message": message}))
			parse.assert_called_once()
		self.assertTrue(frappe.db.exists(DT_ACTIVITY, "-1009100000001:61"))

	def test_counting_errors_never_break_the_handler(self):
		user, _ = self.member(0)
		message = {
			"message_id": 62,
			"from": user,
			"chat": {"id": -1009100000001, "type": "supergroup"},
			"date": 1,
			"text": "hi",
		}
		frappe.flags.in_test = False  # the handler re-raises in tests so bugs are not hidden
		try:
			with patch(
				"telegram_access_manager.bot.handlers.engagement.record_message",
				side_effect=RuntimeError("boom"),
			):
				with patch("telegram_access_manager.bot.handlers.log_error") as logged:
					self.assertFalse(dispatch({"message": message}))
					logged.assert_called_once()
		finally:
			frappe.flags.in_test = True


class TestStatistics(EngagementTestCase):
	def setUp(self):
		super().setUp()
		self.u1, _ = self.member(0)
		self.u2, _ = self.member(1)
		self.u3, _ = self.member(2)
		self.u4, self.head_access = self.member(3, role=self.head, username="motionhead1")
		self.mid = 1000

	def say(self, user, n, on=0):
		for _i in range(n):
			self.mid += 1
			self.msg(self.chat_motion, user, self.mid, when=at(day(on)))

	def stats(self):
		return engagement.compute_stats(self.motion(), MONDAY, day(4))

	def test_totals_participation_and_days(self):
		self.say(self.u1, 30, on=0)  # message counts are never capped
		self.say(self.u1, 2, on=2)
		self.say(self.u2, 1, on=0)
		stats = self.stats()
		self.assertEqual((stats["eligible"], stats["active"], stats["participation_pct"]), (4, 2, 50))
		self.assertEqual(stats["total_messages"], 33)
		self.assertEqual(stats["active_days"], 2)  # Monday and Wednesday
		first = stats["most_active"][0]
		self.assertEqual(
			(first["employee"], first["messages"], first["active_days"]), (self.employees[0], 32, 2)
		)

	def test_consistency_counts_people_active_on_two_or_more_days(self):
		self.say(self.u1, 1, on=0)
		self.say(self.u1, 1, on=1)
		self.say(self.u2, 5, on=3)
		stats = self.stats()
		self.assertEqual([p["employee"] for p in stats["most_consistent"]], [self.employees[0]])
		self.assertEqual(stats["consistent_pct"], 25)

	def test_period_boundaries(self):
		self.say(self.u1, 1, on=-1)  # previous Sunday
		self.say(self.u1, 1, on=5)  # Saturday after the Friday cut-off
		self.assertEqual(self.stats()["total_messages"], 0)

	def test_unmatched_excluded_and_ineligible_senders_are_not_counted(self):
		hr_user, _ = self.member(4, role=self.hr)
		self.say(hr_user, 5)  # recorded (known employee) but HR is excluded from reports
		self.say(self.u1, 2)
		stats = self.stats()
		self.assertEqual(stats["total_messages"], 2)
		self.assertEqual([p["employee"] for p in stats["most_active"]], [self.employees[0]])

	def test_people_who_did_not_participate_are_not_listed(self):
		self.say(self.u1, 1)
		names = {p["employee"] for p in self.stats()["most_active"]}
		self.assertEqual(names, {self.employees[0]})

	def test_large_department_with_few_active_people_is_not_shown_as_fully_active(self):
		self.say(self.u1, 200)
		stats = self.stats()
		self.assertEqual(stats["participation_pct"], 25)  # coverage, not volume

	def test_overall_winner_prefers_coverage_over_volume(self):
		loud = {
			"chat": "a",
			"participation_pct": 25,
			"consistent_pct": 25,
			"total_messages": 500,
			"eligible": 4,
		}
		broad = {
			"chat": "b",
			"participation_pct": 75,
			"consistent_pct": 50,
			"total_messages": 20,
			"eligible": 4,
		}
		self.assertEqual(engagement.pick_winner([loud, broad])["chat"], "b")
		self.assertIsNone(engagement.pick_winner([{**loud, "total_messages": 0}]))


class TestCampaigns(EngagementTestCase):
	def setUp(self):
		super().setUp()
		self.u1, _ = self.member(0)
		self.u2, _ = self.member(1)
		self.u3, self.head_access = self.member(2, role=self.head, username="motionhead1")
		self.u4, self.head_no_username = self.member(3, role=self.head, username=None)
		self.mid = 2000
		# The print department has its own member so it is a second department group.
		self.p1, _ = self.member(5, role=self.print_team)
		self.tg.calls.clear()  # approval notifications are not part of these tests

	def say(self, chat, user, n, on=0):
		for _i in range(n):
			self.mid += 1
			self.msg(chat, user, self.mid, when=at(day(on)))

	def run_campaign(self, campaign, on, **kwargs):
		return engagement.run_campaign(campaign, now=at(day(on), 9), **kwargs)

	def texts_for(self, chat_id):
		return [c["text"] for c in self.sent_texts() if str(c["chat_id"]) == chat_id]

	def test_monday_message_goes_once_to_each_department_group(self):
		results = self.run_campaign("Monday", 0)
		ids = [c["chat_id"] for c in self.sent_texts()]
		self.assertEqual(len(ids), len(set(ids)))  # never the same chat twice
		self.assertTrue({"-1009100000001", "-1009100000002"} <= set(ids))
		self.assertEqual(len(ids), len(engagement.find_departments()))
		self.assertTrue({"-1009100000003", "-1009100000004"}.isdisjoint(ids))  # shared chat and channel
		self.assertEqual({r["status"] for r in results}, {"Sent"})
		self.assertIn("Happy Monday, team!", self.sent_texts()[0]["text"])
		self.assertIn("#EternalCommunity", self.sent_texts()[0]["text"])

	def test_second_run_in_the_same_week_sends_nothing(self):
		self.run_campaign("Monday", 0)
		self.tg.calls.clear()
		results = self.run_campaign("Monday", 0)
		self.assertEqual(self.sent_texts(), [])
		self.assertEqual({r["status"] for r in results}, {"Already handled"})
		self.assertEqual(
			frappe.db.count(DT_ENGAGEMENT_LOG, {"campaign": "Monday", "status": "Sent"}), len(results)
		)

	def test_next_week_is_sent_again(self):
		self.run_campaign("Monday", 0)
		self.tg.calls.clear()
		self.run_campaign("Monday", 7)
		self.assertEqual(len(self.sent_texts()), len(engagement.find_departments()))

	def test_force_resends(self):
		self.run_campaign("Monday", 0)
		self.tg.calls.clear()
		self.run_campaign("Monday", 0, chat=self.chat_motion, force=True)
		self.assertEqual(len(self.sent_texts()), 1)

	def test_wednesday_low_participation_nudges_the_head_with_a_real_mention(self):
		self.say(self.chat_motion, self.u1, 3, on=0)  # 1 of 4 eligible
		self.run_campaign("Wednesday", 2, chat=self.chat_motion)
		text = self.texts_for("-1009100000001")[0]
		self.assertIn("Hey, TAM Motion Team!", text)
		self.assertIn("could use a little boost", text)
		self.assertIn("@motionhead1", text)  # Telegram username
		self.assertIn(f'<a href="tg://user?id={self.u4["id"]}">', text)  # no username: mention by user ID
		self.assertNotIn("Almaz", text)  # nobody is named or shamed

	def test_wednesday_good_participation_gets_appreciation_not_a_warning(self):
		for user in (self.u1, self.u2, self.u3):
			self.say(self.chat_motion, user, 1, on=0)
		self.run_campaign("Wednesday", 2, chat=self.chat_motion)
		text = self.texts_for("-1009100000001")[0]
		self.assertIn("3 of 4 teammates", text)
		self.assertNotIn("ghost town", text)

	def test_wednesday_without_any_head_still_works(self):
		self.run_campaign("Wednesday", 2, chat=self.chat_other)
		self.assertIn("Team Leads, we need your leadership", self.texts_for("-1009100000002")[0])

	def test_wednesday_is_private_to_the_group(self):
		self.run_campaign("Wednesday", 2)
		self.assertTrue(all(str(c["chat_id"]).startswith("-100") for c in self.sent_texts()))

	def test_friday_recognises_active_members_and_the_winning_team(self):
		self.say(self.chat_motion, self.u1, 25, on=0)
		self.say(self.chat_motion, self.u1, 1, on=1)
		self.say(self.chat_motion, self.u2, 3, on=1)
		self.say(self.chat_other, self.p1, 4, on=0)
		self.run_campaign("Friday", 4)
		motion = self.texts_for("-1009100000001")[0]
		self.assertIn("ETERNAL WEEKLY COMMUNITY AWARDS", motion)
		self.assertIn("– 26 messages", motion)  # noqa: RUF001
		self.assertIn("– 3 messages", motion)  # noqa: RUF001
		self.assertIn("active on 2 different days", motion)
		self.assertNotIn("Chaltu", motion)  # silent members are never listed
		other = self.texts_for("-1009100000002")[0]
		self.assertIn("4 messages", other)

	def test_friday_in_a_silent_group_does_not_publish_a_ranking(self):
		self.say(self.chat_other, self.p1, 2, on=0)
		self.run_campaign("Friday", 4, chat=self.chat_motion)
		text = self.texts_for("-1009100000001")[0]
		self.assertIn("quiet week", text)
		self.assertNotIn("Most Active Members", text)

	def test_friday_uses_names_from_erpnext_and_escapes_html(self):
		frappe.db.set_value(
			"Employee Telegram Access",
			{"telegram_user_id": str(self.u1["id"])},
			"employee_name",
			"A <b>&</b> B",
		)
		self.say(self.chat_motion, self.u1, 1)
		self.run_campaign("Friday", 4, chat=self.chat_motion)
		self.assertIn("A &lt;b&gt;&amp;&lt;/b&gt; B", self.texts_for("-1009100000001")[0])

	def test_telegram_failure_is_logged_and_retried_until_the_limit(self):
		self.tg.fail["sendMessage"] = [api_error("sendMessage", 500, "Internal error")]
		first = self.run_campaign("Monday", 0, chat=self.chat_motion)
		self.assertEqual(first[0]["status"], "Failed")
		log = frappe.get_doc(DT_ENGAGEMENT_LOG, f"Monday:{MONDAY}:-1009100000001")
		self.assertEqual((log.status, log.attempts), ("Failed", 1))
		self.assertIn("Internal error", log.error)

		self.assertEqual(
			self.run_campaign("Monday", 0, chat=self.chat_motion)[0]["status"], "Sent"
		)  # next tick succeeds
		self.assertEqual(frappe.db.get_value(DT_ENGAGEMENT_LOG, log.name, "attempts"), 2)

	def test_failure_stops_after_max_attempts(self):
		self.tg.fail["sendMessage"] = [api_error("sendMessage", 500, "down")] * (engagement.MAX_ATTEMPTS + 2)
		for _i in range(engagement.MAX_ATTEMPTS + 1):
			self.run_campaign("Monday", 0, chat=self.chat_motion)
		self.assertEqual(len(self.sent_texts()), engagement.MAX_ATTEMPTS)

	def test_one_failing_group_does_not_stop_the_others(self):
		self.tg.fail["sendMessage"] = [
			api_error("sendMessage", 403, "bot was kicked from the supergroup chat")
		]
		results = self.run_campaign("Monday", 0)
		statuses = [r["status"] for r in results]
		self.assertEqual(statuses.count("Failed"), 1)
		self.assertEqual(statuses.count("Sent"), len(statuses) - 1)

	def test_bot_token_is_not_stored_in_errors(self):
		self.tg.fail["sendMessage"] = [
			api_error("sendMessage", 400, "bad request 123456789:TEST_ONLY_not_a_real_token_xxxxxxxxxx")
		]
		self.run_campaign("Monday", 0, chat=self.chat_motion)
		error = frappe.db.get_value(DT_ENGAGEMENT_LOG, f"Monday:{MONDAY}:-1009100000001", "error")
		self.assertNotIn("TEST_ONLY", error)

	def test_preview_sends_nothing_and_writes_no_log(self):
		preview = engagement.preview("Wednesday", self.chat_motion)
		self.assertIn("Hey, TAM Motion Team!", preview[0]["text"])
		self.assertEqual(self.sent_texts(), [])
		self.assertEqual(frappe.db.count(DT_ENGAGEMENT_LOG), 0)


class TestSchedule(EngagementTestCase):
	def test_windows_follow_the_addis_ababa_clock(self):
		self.assertEqual(engagement.due_campaign(at(day(0), 9, 0)), "Monday")
		self.assertEqual(engagement.due_campaign(at(day(0), 8, 59)), None)
		self.assertEqual(engagement.due_campaign(at(day(0), 11, 59)), "Monday")  # catch-up window
		self.assertIsNone(engagement.due_campaign(at(day(0), 12, 0)))
		self.assertEqual(engagement.due_campaign(at(day(2), 10, 15)), "Wednesday")
		self.assertEqual(engagement.due_campaign(at(day(4), 16, 0)), "Friday")
		self.assertIsNone(engagement.due_campaign(at(day(1), 9, 0)))  # Tuesday

	def test_server_time_zone_does_not_matter(self):
		utc_nine_am_monday = datetime(
			2026, 11, 9, 6, 0, tzinfo=engagement.ZoneInfo("UTC")
		)  # 09:00 in Addis Ababa
		self.assertEqual(engagement.due_campaign(utc_nine_am_monday.astimezone(TZ)), "Monday")

	def test_scheduler_posts_when_due_and_is_idempotent_across_ticks(self):
		self.member(0)
		now = at(day(0), 9, 5)
		self.tg.calls.clear()
		self.assertTrue(engagement.run_scheduled(now))
		self.assertTrue(self.sent_texts())
		self.tg.calls.clear()
		engagement.run_scheduled(at(day(0), 9, 20))
		self.assertEqual(self.sent_texts(), [])

	def test_scheduler_does_nothing_outside_the_window_or_when_disabled(self):
		self.tg.calls.clear()
		self.assertEqual(engagement.run_scheduled(at(day(1), 9, 0)), [])
		self.set_settings(engagement_enabled=0)
		self.assertEqual(engagement.run_scheduled(at(day(0), 9, 0)), [])
		self.assertEqual(self.sent_texts(), [])

	def test_dashboard_reports_departments_and_history(self):
		user, _ = self.member(0)
		self.msg(self.chat_motion, user, 3001)
		with patch.object(engagement, "now_addis", return_value=at(MONDAY)):
			data = engagement.get_dashboard()
		row = next(d for d in data["departments"] if d["chat"] == self.chat_motion)
		self.assertEqual((row["eligible"], row["active"], row["total_messages"]), (1, 1, 1))
		self.assertTrue(data["enabled"])
