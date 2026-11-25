# Telegram Access Manager: Operations Guide

How the Telegram integration works, how to run it locally and in production, and how to fix common problems.
Keep this file next to the code. No tokens or passwords belong in it.

---

## 1. What the system does

Two separate features share one bot:

| Feature | Direction | Purpose |
|---|---|---|
| **Access management** | Telegram → ERP → Telegram | Employees request access to company groups through the bot. HR approves in ERPNext. The bot creates invite links, approves join requests and removes people when access is revoked. |
| **Notification rules** | ERP → Telegram group | ERPNext document events (new Project, status change, and so on) are posted to the Telegram group you choose. |

ERPNext is the source of truth. Telegram only reflects what ERPNext decides.

## 2. Key concepts and records

| Record (DocType) | What it is |
|---|---|
| **Telegram Access Settings** | One settings page: bot token, on/off, update mode, default community, messages. Also reachable at `/telegram-access` (the portal). |
| **Telegram Community** | A set of chats, such as "Team Eternal". Every registered chat belongs to one. |
| **Telegram Chat** | One group, supergroup or channel: numeric chat ID, type, active flag, bot rights. |
| **Telegram Access Role** | A bundle of chats (HR Team, Production Team...) given to employees. |
| **Telegram Access Request** | An employee's request. Statuses include pending, approved, rejected. |
| **Telegram Invite Link** | Links the bot created. Stored encrypted. |
| **Telegram Access Audit Log** | Append-only history of requests, decisions and failures. Never contains tokens. |
| **Telegram Bot Update** | One row per Telegram update, used to avoid processing duplicates. Purged after 30 days. |
| **Telegram Notification Rule** | A route from an ERP event to one Telegram chat (section 7). |

## 3. Access flow (employee → group)

1. An employee opens the bot and sends `/start`, then presses **Request access**.
2. The bot records their Telegram ID, username and display name, then creates a request.
3. HR/managers review it in the portal and approve or reject it. Roles decide which chats the employee gets.
4. On approval the bot sends personal invite links by private message. Links expire after 24 hours by default.
5. When the employee uses a link, Telegram sends a join request. The bot approves it only if ERPNext says the access is valid. Unauthorized join requests are declined.
6. On revocation the bot removes the member where it has the right to. Admins and owners can't be removed by a bot, so remove them by hand.

Bot commands: `/start`, `/request`, `/status`, `/access`, `/help`, and `/register` (admins, in a group).

## 4. How the bot receives Telegram messages (the important part)

A bot can receive its updates in **only one place** at a time.

| Mode | Used for | How it works |
|---|---|---|
| **Webhook** | Production | Telegram calls `https://<public-site>/api/method/telegram_access_manager.api.webhook.telegram_webhook`. The site must be reachable over valid HTTPS. |
| **Polling** | Local development | A process (`bench ... telegram-poll`) asks Telegram for updates repeatedly. Telegram refuses polling while a webhook is set. |

**Rules**

- Never use the same bot for production and local. Whichever has the webhook (or poller) gets every message.
- Use a separate test bot for development.
- Never run a poller and a webhook for the same bot together.

### Bots in use

| Bot | Used by | Mode |
|---|---|---|
| `@EMC_Community_bot` | Production | Webhook |
| `@testemcfornotification_bot` | Local ERP | Polling |

The bot token is read in this order: environment variable `TELEGRAM_BOT_TOKEN`, then Telegram Access Settings, then `site_config.json`. If a token seems to be ignored, check for an environment variable first.

## 5. Setting up a new bot and group

1. In Telegram, open **@BotFather**, send `/newbot` and copy the token. Treat the token as a password.
2. Add the bot to each group as an **administrator** with **Invite users via link** and **Ban users**. Convert basic groups to supergroups first.
3. In ERPNext, open Telegram Access Settings. Paste the token under *Replace bot token*, save and press **Test connection**. The "Connected as @..." line must show the bot you expect.
4. Choose the update mode (section 4). Turn the bot on.
5. In the group, an admin sends `/register` (not anonymously). For a channel, send `/register` to the bot privately and post the code it gives you in the channel.
6. In ERPNext open **Chats Awaiting Review**, press **Validate Bot Access**, tick **Active**, save. Registering never grants access by itself.
7. Add the chat to a **Telegram Access Role**. Give employees the link `https://t.me/<bot_username>`.

Supergroup and channel IDs start with `-100`.

## 6. Local environment (this machine)

| Item | Value |
|---|---|
| Stack | Docker Compose project `tamdev`, file `C:\Users\User\tam_work\docker\compose.yml` |
| ERP address | **http://127.0.0.1:8088/desk** (use `127.0.0.1`, not `localhost`) |
| Site name | `tam.localhost` |
| Portal | http://127.0.0.1:8088/telegram-access |
| App code | `C:\Users\User\tam_work\repo`, mounted into the containers (edits are live) |
| Containers | backend, frontend (nginx), worker, scheduler, websocket, db, redis x2 |

**Start the ERP after a reboot**

```
docker compose -p tamdev start
```

**Start the poller (needed for local bot messages).** Run it again after any container restart:

```
docker exec -d tamdev-backend-1 bench --site tam.localhost telegram-poll
```

**After changing app code or adding a DocType**

```
docker exec tamdev-backend-1 bench --site tam.localhost migrate
docker compose -p tamdev restart backend worker scheduler
```

Restarting the backend stops the poller, so start it again.

## 7. Notifications from ERPNext to Telegram groups

Create a **Telegram Notification Rule** per route (`/desk/telegram-notification-rule/new`).

| Field | Meaning |
|---|---|
| Document Type | Which ERP document to watch, such as Project |
| Send When | New, Save, Submit, Cancel, or Value Change (then also name the field to watch) |
| Telegram Group / Channel | The target **Telegram Chat** record |
| Condition | Optional Python expression. Send only if true, for example `doc.department == "Event - EMC"` |
| Message Template | Optional Jinja, for example `New project {{ doc.project_name }}`. Values are HTML-escaped. Empty means a default message |
| Add Link to Document | Appends an "Open in ERP" link |

Use **Send Test Message** on a rule to check that the bot can post to the group. The rule shows *Last Sent On* and *Last Error*.

**Example: one rule per department**

- Rule "Event projects": Project, New, chat = Event group, condition `doc.department == "Event - EMC"`
- Rule "Production projects": Project, New, chat = Production group, condition `doc.department == "Production - EMC"`

**How it works inside**

- A doc-event hook on every DocType calls `services/notification_rules.py`. It uses a cached list of enabled rules, so unrelated saves cost almost nothing.
- Matching rules queue a background job after the database commit. The job renders the message and calls the Bot API.
- Failures are written to the rule's *Last Error*. They never block the save that triggered them.
- The background **worker** container must be running, or notifications will not be sent.
- The target chat must have the bot as a member, and as an admin for channels.

## 8. Production notes

- Set **Public Base URL** in settings, then press **Set Webhook**. Use **Webhook Info** to check `pending_update_count` and `last_error_message`.
- On Dokploy, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` as environment variables on **every** service that runs app code (backend, worker, scheduler), then restart them.
- After deploying new code, run a migrate on the production site.
- The token and webhook secret live only on the server. Restoring a backup to another site means re-entering the token, because the encrypted values can't be decrypted there.
- Switching bots: paste the new token, save, restart the poller or press **Set Webhook**, make the bot admin in each chat (**Validate Bot Access**), then ask employees to press Start. Revoke the old token in @BotFather.

## 9. Troubleshooting (including problems met during setup)

| Symptom | Cause and fix |
|---|---|
| Browser: "localhost didn't send any data" (`ERR_EMPTY_RESPONSE`) | `localhost` resolves to IPv6, where a WSL relay drops the connection. Use `127.0.0.1:8088`. |
| `/telegram-access` shows the login page | You are signed out. Log in. You also need the Telegram Access Manager role (or another verifier role). |
| Portal blank, console shows CSS 404 with "MIME type text/html" | The frontend container has no mount for the app's built files. `compose.yml` must mount `../repo/telegram_access_manager/public` at `/home/frappe/frappe-bench/assets/telegram_access_manager`. |
| Requests arrive on production instead of local | The local site is using the production bot. Give local its own test bot and token. Check "Connected as @..." in settings. |
| New token saved but behavior unchanged | Check the settings page shows the new bot username. Check no `TELEGRAM_BOT_TOKEN` environment variable overrides it. |
| Bot doesn't answer locally | Settings "Bot is on" off, poller not running (restart it), or the mode is wrong. See *Telegram Bot Update* and the Error Log. |
| Poller: "A webhook is set" / 409 Conflict | Delete the webhook in settings first. No other poller or deployment may use the same bot. |
| Webhook Info: 403 | Secret mismatch. Press **Set Webhook** again. |
| Webhook Info: SSL or connection refused | The site isn't publicly reachable over valid HTTPS. |
| `/register` does nothing in a group | Another bot was addressed, the poller runs old code (restart it), or it was rate-limited. |
| Chat validation: "chat not found" | Wrong ID (missing `-100`), bot not in the chat, or the group became a supergroup with a new ID. |
| "not enough rights" / bot status Member | Promote the bot to admin with **Invite users via link** and **Ban users**, then **Validate Bot Access**. |
| Notification rule sends nothing | Rule not enabled, condition false, worker container down, bot not in the chat, or see the rule's *Last Error* and the Error Log. |
| Employee notification Failed | The employee blocked the bot or never pressed Start. Ask them to open the bot, then **Resend Notification**. |

## 10. Security checklist

- Treat bot tokens like passwords. Never paste them in chat, tickets or documents. If one leaks, revoke it in @BotFather.
- Do not share Telegram chat-folder (`t.me/addlist/...`) or community links. They bypass ERPNext approval.
- Keep production and test bots separate.
- Back up the database regularly. The audit log is your history of who got access and why.

## 11. Code map

| Path | Purpose |
|---|---|
| `telegram_access_manager/hooks.py` | App hooks: doc events, scheduled jobs, website routes |
| `telegram_access_manager/telegram/client.py` | Bot API client |
| `telegram_access_manager/bot/handlers.py` | Bot commands and updates |
| `telegram_access_manager/api/webhook.py` | Webhook endpoint |
| `telegram_access_manager/services/` | Business logic. `notification_rules.py` is the ERP-to-group routing. |
| `telegram_access_manager/tasks.py` | Scheduled jobs: retry failed syncs, expire links, purge old data, engagement posts |
| `telegram_access_manager/telegram_access_manager/doctype/` | All DocTypes |
| `frontend/` | The Vue portal served at `/telegram-access` |
| `README.md` | Original, more detailed reference |
