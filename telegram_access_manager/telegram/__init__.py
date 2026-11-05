import threading

import requests

from telegram_access_manager.credentials import get_bot_token
from telegram_access_manager.telegram.client import TelegramAPIError, TelegramClient, redact

_local = threading.local()


def get_client() -> TelegramClient:
	"""Return a client for the configured bot. Raises TelegramConfigError if not configured.

	Every caller goes through this factory so tests can patch it with a mock client.

	The HTTP session is kept per thread so consecutive calls reuse one connection to Telegram instead of
	opening a new TLS connection each time (a large part of the delay before the bot answers).
	"""
	session = getattr(_local, "session", None)
	if session is None:
		session = _local.session = requests.Session()
	return TelegramClient(get_bot_token(), session=session)
