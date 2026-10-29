from unittest.mock import MagicMock

import requests
from frappe.tests import UnitTestCase

from telegram_access_manager.telegram.client import TelegramAPIError, TelegramClient, redact

TOKEN = "123456789:TEST_ONLY_not_a_real_token_xxxxxxxxxx"


def _response(payload, status=200):
	resp = MagicMock()
	resp.status_code = status
	resp.json.return_value = payload
	return resp


class TestTelegramClient(UnitTestCase):
	def test_success_returns_result(self):
		session = MagicMock()
		session.post.return_value = _response({"ok": True, "result": {"id": 1}})
		client = TelegramClient(TOKEN, session=session)
		self.assertEqual(client.get_me(), {"id": 1})
		url = session.post.call_args.args[0]
		self.assertTrue(url.endswith("/getMe"))

	def test_none_params_are_not_sent(self):
		session = MagicMock()
		session.post.return_value = _response({"ok": True, "result": {"invite_link": "x"}})
		TelegramClient(TOKEN, session=session).create_chat_invite_link(-100, "n", 1, member_limit=None, creates_join_request=True)
		self.assertNotIn("member_limit", session.post.call_args.kwargs["json"])
		self.assertTrue(session.post.call_args.kwargs["json"]["creates_join_request"])

	def test_api_error_is_parsed(self):
		session = MagicMock()
		session.post.return_value = _response(
			{"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 7", "parameters": {"retry_after": 7}},
			429,
		)
		with self.assertRaises(TelegramAPIError) as ctx:
			TelegramClient(TOKEN, session=session).send_message(1, "hi")
		self.assertEqual(ctx.exception.error_code, 429)
		self.assertEqual(ctx.exception.retry_after, 7)
		self.assertTrue(ctx.exception.retryable)

	def test_client_errors_are_not_retryable(self):
		self.assertFalse(TelegramAPIError("getChat", 400, "Bad Request: chat not found").retryable)
		self.assertFalse(TelegramAPIError("getChat", 403, "Forbidden").retryable)
		self.assertTrue(TelegramAPIError("getChat", 502, "Bad Gateway").retryable)
		self.assertTrue(TelegramAPIError("getChat", None, "Network error").retryable)

	def test_network_error_never_leaks_token(self):
		session = MagicMock()
		session.post.side_effect = requests.ConnectionError(f"Failed for https://api.telegram.org/bot{TOKEN}/getMe")
		with self.assertRaises(TelegramAPIError) as ctx:
			TelegramClient(TOKEN, session=session).get_me()
		self.assertNotIn(TOKEN, str(ctx.exception))
		self.assertIsNone(ctx.exception.__cause__)
		self.assertTrue(ctx.exception.__suppress_context__)

	def test_repr_hides_token(self):
		self.assertNotIn(TOKEN, repr(TelegramClient(TOKEN)))

	def test_redact(self):
		text = f"token {TOKEN} link https://t.me/+AbCdEf123 and t.me/joinchat/XyZ"
		out = redact(text)
		self.assertNotIn(TOKEN, out)
		self.assertNotIn("AbCdEf123", out)
		self.assertNotIn("XyZ", out)
