"""bench commands:

bench --site <site> telegram-set-secrets   # hidden prompt, stores secrets in the site's site_config.json
bench --site <site> telegram-poll          # development-only long polling (never together with a webhook)
"""

import secrets
import time

import click
from frappe.commands import get_site, pass_context


@click.command("telegram-set-secrets")
@click.option("--token/--no-token", default=True, help="Prompt for the bot token (input is hidden).")
@click.option("--generate-webhook-secret/--keep-webhook-secret", default=True)
@pass_context
def telegram_set_secrets(context, token, generate_webhook_secret):
	"""Store the bot token / webhook secret in site_config.json without echoing them or using shell history."""
	import frappe
	from frappe.installer import update_site_config

	from telegram_access_manager.credentials import SECRET_CONF, TOKEN_CONF, TOKEN_RE

	site = get_site(context)
	frappe.init(site)
	try:
		if token:
			value = click.prompt("Telegram bot token", hide_input=True).strip()
			if not TOKEN_RE.match(value):
				raise click.ClickException("That does not look like a bot token (expected <digits>:<secret>).")
			update_site_config(TOKEN_CONF, value)
			click.echo("Bot token saved to site_config.json.")
		if generate_webhook_secret:
			update_site_config(SECRET_CONF, secrets.token_urlsafe(48))
			click.echo("New webhook secret generated. Run 'Set Webhook' in Telegram Access Settings to register it.")
	finally:
		frappe.destroy()


@click.command("telegram-poll")
@click.option("--once", is_flag=True, help="Fetch one batch of updates and exit.")
@click.option("--timeout", default=25, type=int, help="Long-polling timeout in seconds.")
@pass_context
def telegram_poll(context, once, timeout):
	"""Development alternative to the webhook. Refuses to run while a webhook is configured."""
	import frappe

	from telegram_access_manager import telegram as tg
	from telegram_access_manager.bot.ingest import ingest_update
	from telegram_access_manager.constants import ALLOWED_UPDATES
	from telegram_access_manager.telegram.client import TelegramAPIError

	site = get_site(context)
	frappe.init(site)
	frappe.connect()
	lock_key = frappe.cache.make_key("tam:poller")
	try:
		settings = frappe.get_single("Telegram Access Settings")
		if not settings.enabled or settings.update_mode != "Polling":
			raise click.ClickException("Enable the integration and set Update Mode = Polling in Telegram Access Settings first.")
		client = tg.get_client()
		if (client.get_webhook_info() or {}).get("url"):
			raise click.ClickException("A webhook is set for this bot. Delete it (Telegram Access Settings) before polling.")
		if not frappe.cache.set(lock_key, "1", nx=True, ex=timeout + 60):
			raise click.ClickException("Another telegram-poll process is already running for this site.")

		offset_key = "tam:poll_offset"
		offset = frappe.cache.get_value(offset_key)
		click.echo("Polling Telegram for updates (Ctrl+C to stop)...")
		while True:
			frappe.cache.expire(lock_key, timeout + 60)
			try:
				updates = client.get_updates(offset, timeout, ALLOWED_UPDATES)
			except TelegramAPIError as e:
				click.echo(f"Telegram error: {e.description}", err=True)
				if e.error_code == 409:
					raise click.ClickException("Conflict: a webhook or another poller is active.")
				time.sleep(min(e.retry_after or 5, 60))
				continue
			for update in updates or []:
				outcome = ingest_update(update, enqueue=False)
				frappe.db.commit()
				offset = update["update_id"] + 1
				frappe.cache.set_value(offset_key, offset)
				click.echo(f"update {update.get('update_id')}: {outcome}")
			if once:
				break
	except KeyboardInterrupt:
		pass
	finally:
		frappe.cache.delete(lock_key)
		frappe.destroy()


commands = [telegram_set_secrets, telegram_poll]
