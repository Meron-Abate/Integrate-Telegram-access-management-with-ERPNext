import os
from unittest.mock import patch

import frappe

from telegram_access_manager.bot.ingest import ingest_update
from telegram_access_manager.constants import DT_UPDATE, SETTINGS
from telegram_access_manager.credentials import TelegramConfigError, get_bot_token, get_config_status, get_webhook_secret
from telegram_access_manager.tests.utils import MANAGER, VERIFIER, FakeTelegram, TAMTestCase

UI_TOKEN = "555666777:TEST_ONLY_token_entered_in_settings_x"
OTHER_TOKEN = "888999000:TEST_ONLY_token_of_another_bot_xxxxx"


class TestSettingsCredentials(TAMTestCase):
	"""Bot credentials entered on the Settings screen (encrypted Password fields)."""

	def setUp(self):
		super().setUp()
		# Use only the Settings screen: no environment variable, no site_config value.
		env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_WEBHOOK_SECRET": ""})
		env.start()
		self.addCleanup(env.stop)
		conf = patch.object(frappe, "conf", frappe._dict())
		conf.start()
		self.addCleanup(conf.stop)

	def test_token_from_settings_is_used_and_stored_encrypted(self):
		self.set_settings(bot_token=UI_TOKEN)
		self.assertEqual(get_bot_token(), UI_TOKEN)
		self.assertEqual(get_config_status()["token_source"], "Settings")

		stored = frappe.db.get_single_value(SETTINGS, "bot_token")
		self.assertNotIn("TEST_ONLY", stored or "", "only a placeholder is stored in the settings table")
		self.assertEqual(frappe.get_single(SETTINGS).bot_token.strip("*"), "", "the document never carries the token")
		history = " ".join(frappe.get_all("Version", filters={"ref_doctype": SETTINGS}, pluck="data"))
		self.assertNotIn("TEST_ONLY", history, "the token must not leak into the change history")

	def test_unchanged_placeholder_keeps_token(self):
		self.set_settings(bot_token=UI_TOKEN)
		doc = frappe.get_single(SETTINGS)  # bot_token now holds *****
		doc.support_contact = "HR desk"
		doc.save()
		self.assertEqual(get_bot_token(), UI_TOKEN)

	def test_invalid_values_rejected(self):
		self.assertRaises(frappe.ValidationError, self.set_settings, bot_token="not-a-token")
		self.assertRaises(frappe.ValidationError, self.set_settings, webhook_secret="short")

	def test_environment_overrides_settings_and_site_config_is_fallback(self):
		self.set_settings(bot_token=UI_TOKEN)
		with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": OTHER_TOKEN}):
			self.assertEqual(get_bot_token(), OTHER_TOKEN)
			self.assertEqual(get_config_status()["token_source"], "environment")
		with (
			patch("telegram_access_manager.credentials._settings_secret", return_value=None),
			patch.object(frappe, "conf", frappe._dict(telegram_bot_token=OTHER_TOKEN)),
		):
			self.assertEqual(get_bot_token(), OTHER_TOKEN)
			self.assertEqual(get_config_status()["token_source"], "site_config")

	def test_missing_everywhere_raises(self):
		with patch("telegram_access_manager.credentials._settings_secret", return_value=None):
			self.assertRaises(TelegramConfigError, get_bot_token)
			self.assertRaises(TelegramConfigError, get_webhook_secret)

	def test_changing_bot_resets_identity_and_poll_offset(self):
		self.set_settings(bot_token=UI_TOKEN)
		frappe.db.set_single_value(SETTINGS, {"bot_id": "555666777", "bot_username": "old_bot"})
		frappe.cache.set_value("tam:poll_offset", 515133999)
		self.tg.calls.clear()
		self.set_settings(bot_token=OTHER_TOKEN)
		self.assertIsNone(frappe.cache.get_value("tam:poll_offset"))
		settings = frappe.get_single(SETTINGS)
		# The (mocked) Telegram getMe call confirmed the new bot identity.
		self.assertEqual((settings.bot_id, settings.bot_username), (str(FakeTelegram.BOT_ID), "tam_test_bot"))
		self.assertEqual(len(self.tg.calls_for("getMe")), 1)

	def test_generate_webhook_secret(self):
		frappe.set_user(MANAGER)
		frappe.get_single(SETTINGS).generate_webhook_secret()
		first = get_webhook_secret()
		frappe.get_single(SETTINGS).generate_webhook_secret()
		self.assertNotEqual(first, get_webhook_secret())
		self.assertGreaterEqual(len(first), 32)
		frappe.set_user(VERIFIER)
		self.assertRaises(frappe.PermissionError, frappe.get_single(SETTINGS).generate_webhook_secret)

	def test_update_tracking_is_per_bot(self):
		update = {"update_id": 777000111, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "text": "hi"}}
		self.set_settings(bot_token=UI_TOKEN)
		self.assertEqual(ingest_update(update, enqueue=False), "accepted")
		self.assertEqual(ingest_update(update, enqueue=False), "duplicate")
		self.set_settings(bot_token=OTHER_TOKEN)
		self.assertEqual(ingest_update(update, enqueue=False), "accepted", "same number from a different bot is a new update")
		self.assertEqual(frappe.db.count(DT_UPDATE, {"update_id": ["like", "%-777000111"]}), 2)
