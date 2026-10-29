"""Minimal, dependency-light Telegram Bot API client.

A direct HTTPS integration (``requests`` is already part of the Frappe environment) keeps the
integration synchronous, which suits Frappe request handlers and RQ workers, and avoids pulling an
asyncio framework into the bench.

Security notes:
- The bot token is part of every request URL. Exceptions raised by ``requests`` include the URL, so
  they are never propagated; ``TelegramAPIError`` carries only the method name and Telegram's
  (redacted) error description.
- Request parameters are never logged because they can contain private invite links.
"""

import re

import requests

API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 15

_TOKEN_RE = re.compile(r"\d{5,20}:[A-Za-z0-9_-]{30,64}")
_INVITE_RE = re.compile(r"(https?://)?(t\.me|telegram\.me|telegram\.dog)/(\+|joinchat/)[A-Za-z0-9_-]+", re.I)


def redact(text) -> str:
	"""Remove bot tokens and private invite links from text before it is stored or logged."""
	if text is None:
		return ""
	text = str(text)
	text = _TOKEN_RE.sub("[REDACTED_TOKEN]", text)
	text = _INVITE_RE.sub("[REDACTED_INVITE_LINK]", text)
	return text


class TelegramAPIError(Exception):
	def __init__(
		self,
		method: str,
		error_code: int | None,
		description: str,
		retry_after: int | None = None,
		migrate_to_chat_id: int | None = None,
	):
		self.method = method
		self.error_code = error_code
		self.description = redact(description)
		self.retry_after = retry_after
		self.migrate_to_chat_id = migrate_to_chat_id
		super().__init__(f"Telegram API {method} failed ({error_code}): {self.description}")

	@property
	def retryable(self) -> bool:
		# Network errors (no code), flood control and server-side errors are transient.
		return self.error_code is None or self.error_code == 429 or self.error_code >= 500

	def matches(self, *fragments: str) -> bool:
		desc = self.description.lower()
		return any(fragment.lower() in desc for fragment in fragments)


class TelegramClient:
	def __init__(self, token: str, timeout: int = DEFAULT_TIMEOUT, session: requests.Session | None = None):
		if not token:
			raise ValueError("A bot token is required")
		self._token = token
		self.timeout = timeout
		self.session = session or requests.Session()

	def __repr__(self):  # never leak the token through repr()
		return "<TelegramClient>"

	def call(self, method: str, _http_timeout: int | None = None, **params):
		url = f"{API_BASE}/bot{self._token}/{method}"
		payload = {k: v for k, v in params.items() if v is not None}
		try:
			response = self.session.post(url, json=payload, timeout=_http_timeout or self.timeout)
		except requests.RequestException as exc:
			# Do not chain the original exception: its message contains the URL (and the token).
			raise TelegramAPIError(method, None, f"Network error: {type(exc).__name__}") from None

		try:
			data = response.json()
		except ValueError:
			raise TelegramAPIError(method, response.status_code, "Invalid (non-JSON) response from Telegram")

		if not isinstance(data, dict) or not data.get("ok"):
			data = data if isinstance(data, dict) else {}
			parameters = data.get("parameters") or {}
			raise TelegramAPIError(
				method,
				data.get("error_code") or response.status_code,
				data.get("description") or "Unknown error",
				retry_after=parameters.get("retry_after"),
				migrate_to_chat_id=parameters.get("migrate_to_chat_id"),
			)
		return data.get("result")

	# --- bot & webhook -------------------------------------------------------------------------
	def get_me(self):
		return self.call("getMe")

	def set_webhook(self, url: str, secret_token: str, allowed_updates: list[str], drop_pending_updates=False):
		return self.call(
			"setWebhook",
			url=url,
			secret_token=secret_token,
			allowed_updates=allowed_updates,
			drop_pending_updates=drop_pending_updates,
			max_connections=10,
		)

	def delete_webhook(self, drop_pending_updates=False):
		return self.call("deleteWebhook", drop_pending_updates=drop_pending_updates)

	def get_webhook_info(self):
		return self.call("getWebhookInfo")

	def get_updates(self, offset: int | None, timeout: int, allowed_updates: list[str]):
		# "timeout" here is Telegram's long-polling timeout; the HTTP timeout must exceed it.
		return self.call(
			"getUpdates",
			_http_timeout=timeout + 10,
			offset=offset,
			timeout=timeout,
			allowed_updates=allowed_updates,
		)

	# --- messages ------------------------------------------------------------------------------
	def send_message(self, chat_id, text: str, reply_markup: dict | None = None):
		return self.call(
			"sendMessage",
			chat_id=chat_id,
			text=text,
			parse_mode="HTML",
			reply_markup=reply_markup,
			link_preview_options={"is_disabled": True},
		)

	def delete_message(self, chat_id, message_id: int):
		return self.call("deleteMessage", chat_id=chat_id, message_id=message_id)

	def answer_callback_query(self, callback_query_id: str, text: str | None = None):
		return self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)

	# --- chats ---------------------------------------------------------------------------------
	def get_chat(self, chat_id):
		return self.call("getChat", chat_id=chat_id)

	def get_chat_member(self, chat_id, user_id):
		return self.call("getChatMember", chat_id=chat_id, user_id=user_id)

	def create_chat_invite_link(
		self, chat_id, name: str, expire_date: int | None, member_limit=None, creates_join_request=None
	):
		return self.call(
			"createChatInviteLink",
			chat_id=chat_id,
			name=name[:32],
			expire_date=expire_date,
			member_limit=member_limit,
			creates_join_request=creates_join_request,
		)

	def revoke_chat_invite_link(self, chat_id, invite_link: str):
		return self.call("revokeChatInviteLink", chat_id=chat_id, invite_link=invite_link)

	def approve_chat_join_request(self, chat_id, user_id):
		return self.call("approveChatJoinRequest", chat_id=chat_id, user_id=user_id)

	def answer_chat_join_request_query(self, query_id: str, result: str):
		# Bot API 10.x: join requests carrying a query_id must be answered within 10 seconds.
		if result not in ("approve", "decline", "queue"):
			raise ValueError("result must be approve, decline or queue")
		return self.call("answerChatJoinRequestQuery", chat_join_request_query_id=query_id, result=result)

	def decline_chat_join_request(self, chat_id, user_id):
		return self.call("declineChatJoinRequest", chat_id=chat_id, user_id=user_id)

	def ban_chat_member(self, chat_id, user_id):
		return self.call("banChatMember", chat_id=chat_id, user_id=user_id)

	def unban_chat_member(self, chat_id, user_id):
		return self.call("unbanChatMember", chat_id=chat_id, user_id=user_id, only_if_banned=True)

