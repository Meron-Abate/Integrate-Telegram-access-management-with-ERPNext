from telegram_access_manager.credentials import get_bot_token
from telegram_access_manager.telegram.client import TelegramAPIError, TelegramClient, redact


def get_client() -> TelegramClient:
	"""Return a client for the configured bot. Raises TelegramConfigError if not configured.

	Every caller goes through this factory so tests can patch it with a mock client.
	"""
	return TelegramClient(get_bot_token())
