from frappe.tests import UnitTestCase

from telegram_access_manager.services.permissions import resolve_effective


class TestEffectivePermissionRules(UnitTestCase):
	"""Pure precedence rules: individual Deny > individual Allow > role; inactive chats never granted."""

	def setUp(self):
		self.active = {"Production", "Equipment", "Announcements", "Management", "Finance"}

	def test_role_chats_are_allowed(self):
		res = resolve_effective(["Production", "Equipment"], {}, self.active)
		self.assertEqual({c: r["effective"] for c, r in res.items()}, {"Production": "Allow", "Equipment": "Allow"})
		self.assertTrue(all(r["source"] == "Role" for r in res.values()))

	def test_documented_example(self):
		# Role: Production Team = Production, Equipment, Announcements
		# Individual: Equipment = Deny, Management = Allow
		res = resolve_effective(
			["Production", "Equipment", "Announcements"], {"Equipment": "Deny", "Management": "Allow"}, self.active
		)
		self.assertEqual(res["Production"]["effective"], "Allow")
		self.assertEqual(res["Production"]["source"], "Role")
		self.assertEqual(res["Equipment"]["effective"], "Deny")
		self.assertEqual(res["Equipment"]["source"], "Individual")
		self.assertEqual(res["Announcements"]["effective"], "Allow")
		self.assertEqual(res["Management"]["effective"], "Allow")
		self.assertEqual(res["Management"]["source"], "Individual")
		self.assertNotIn("Finance", res)

	def test_deny_overrides_role_and_allow_not_needed(self):
		res = resolve_effective(["Production"], {"Production": "Deny"}, self.active)
		self.assertEqual(res["Production"]["effective"], "Deny")

	def test_explicit_allow_on_role_chat_is_still_allow(self):
		res = resolve_effective(["Production"], {"Production": "Allow"}, self.active)
		self.assertEqual(res["Production"]["effective"], "Allow")

	def test_inherit_without_role_is_denied(self):
		res = resolve_effective([], {"Finance": "Inherit"}, self.active)
		self.assertEqual(res["Finance"]["effective"], "Deny")
		self.assertEqual(res["Finance"]["source"], "None")

	def test_inactive_chat_is_never_granted(self):
		res = resolve_effective(["Production"], {"Management": "Allow"}, {"Equipment"})
		self.assertEqual(res["Production"]["effective"], "Deny")
		self.assertEqual(res["Management"]["effective"], "Deny")

	def test_granting_one_chat_never_grants_another(self):
		res = resolve_effective([], {"Management": "Allow"}, self.active)
		self.assertEqual([c for c, r in res.items() if r["effective"] == "Allow"], ["Management"])

	def test_invalid_override_falls_back_to_inherit(self):
		res = resolve_effective([], {"Finance": "Everything"}, self.active)
		self.assertEqual(res["Finance"]["effective"], "Deny")

	def test_no_duplicate_results(self):
		res = resolve_effective(["Production", "Production"], {"Production": "Inherit"}, self.active)
		self.assertEqual(list(res), ["Production"])
