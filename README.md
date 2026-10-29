# Telegram Access Manager

A Frappe/ERPNext app (Frappe v16, ERPNext v16) that controls which employees can join which company Telegram
groups and channels. ERPNext is the source of truth. The bot collects access requests and carries out
the decisions administrators make in ERPNext. It never decides on its own who gets in.

- [How it works](#how-it-works)
- [Data model](#data-model)
- [Permission precedence](#permission-precedence)
- [Installation (existing site)](#installation-existing-site)
- [Secrets configuration](#secrets-configuration)
- [Telegram setup](#telegram-setup)
- [ERPNext roles](#erpnext-roles)
- [Day-to-day administration](#day-to-day-administration)
- [Local development](#local-development)
- [Production deployment with Dokploy](#production-deployment-with-dokploy)
- [Troubleshooting](#troubleshooting)
- [Telegram limitations and manual steps](#telegram-limitations-and-manual-steps)
- [Security notes](#security-notes)
- [Tests](#tests)

## How it works

1. An employee opens the bot and sends `/start`. The bot shows a privacy notice and a **Request access** button.
2. The bot records the Telegram numeric user ID, username (if the employee has one), display name and
   request time in a **Telegram Access Request** (status *Pending Verification*). Repeated `/start`s,
   repeated button presses and re-delivered updates reuse the same open request.
3. A **Telegram Access Verifier** (or Manager) matches the request to an **Employee** and confirms,
   through a trusted channel, that the Telegram account belongs to that person. The username is not
   accepted as proof. The verifier records the method used (in person, video call, phone call to the
   number on the Employee record, line-manager confirmation, other).
4. A **Telegram Access Manager** picks a **Telegram Access Role**, adds individual Allow/Deny
   overrides, reviews the effective-permission preview and approves.
5. Approval creates (or reactivates) an **Employee Telegram Access** record. A background job then:
   - asks Telegram for the employee's current membership in each allowed chat;
   - creates a personal, expiring invite link for each allowed chat the employee hasn't joined yet;
   - sends those links privately to the employee.
6. When the employee uses a link, Telegram sends a *join request* to the bot. The bot approves it
   only if ERPNext shows an explicit **Allow** for that user and chat right now. Every other request
   in a managed chat is declined (configurable).
7. Membership updates from Telegram (`chat_member`) record the **actual** membership, kept separate
   from the **desired** permission.
8. Revocations (individual Deny, role changes, an employee leaving) revoke the invite links and
   remove the member where the bot has the rights. Every step is written to the **Telegram Access
   Audit Log**.

## Data model

| DocType | Purpose |
|---|---|
| Telegram Community | Organizational container (name, description, informational link/identifier). Grants nothing. |
| Telegram Chat | One group/supergroup/channel: numeric chat ID (stored as text, negative IDs supported), type, community, active flag, access method, bot status/rights, last validation/sync, last error. |
| Telegram Access Role | Reusable bundle of chats (child table *Telegram Access Role Chat*). Not an ERPNext Role. |
| Telegram Access Request | `TAR-YYYY-#####`. Captured identity, employee match, verification metadata, role, per-chat overrides (*Telegram Access Request Chat*), decision, notification status, errors. |
| Employee Telegram Access | `ETA-#####`. One per employee (unique `employee`) and one per Telegram user ID (unique `telegram_user_id`). Holds the verified identity, role, overrides and per-chat state (*Employee Telegram Chat Permission*: source, override, effective/desired permission, actual membership, sync status, attempts, next retry, last error). |
| Telegram Invite Link | Every link the bot created. The link itself is an encrypted Password field, readable only by server code. |
| Telegram Access Audit Log | Append-only trail of requests, decisions, grants, removals, failures and configuration changes. Never contains tokens or invite links. |
| Telegram Bot Update | Deduplication record per Telegram `update_id` (no message content stored). Purged after N days. |
| Telegram Access Settings | Single doctype with integration options. Contains **no secrets**. |

The numeric Telegram user ID is the identity key. Usernames can change or be missing, so they are
refreshed automatically and an *Identity Updated* audit entry is written.

## Permission precedence

For each chat, the effective (desired) permission is:

1. **Individual Deny** → Deny, even if the role allows the chat.
2. **Individual Allow** → Allow, even if the role doesn't include the chat.
3. **Inherit** → Allow only if the assigned, *active* Telegram Access Role lists the chat.
4. A chat that is **inactive** in ERPNext is never granted.

Nothing else grants access. Community membership, membership of another chat, or a matching
username has no effect. A revoked Employee Telegram Access denies every chat.

Example. Role *Production Team* = Production Group, Equipment Group, Company Announcements.
Individual overrides: Equipment Group = Deny, Management Channel = Allow.
Result: Production ✅ (role), Equipment ❌ (individual deny), Announcements ✅ (role), Management ✅ (individual).

The request and access forms show this preview before approval and after every change.

**Recalculation.** Changing a role's chats, deactivating a role or chat, or editing overrides only
recalculates the *desired* state and marks the record **Changes Pending**. Telegram isn't touched
until a manager clicks **Apply to Telegram** (on the access record) or **Apply Pending Changes to
Telegram** (on the role). You can enable *Automatically Apply Role Changes* in settings if you prefer.

## Installation (existing site)

These steps were used on the Frappe Manager bench (`/workspace/frappe-bench`, site `emc.localhost`).
Run them inside the bench container (`fm shell emc.localhost`, or
`docker exec -it -u frappe -w /workspace/frappe-bench fm__emc_localhost__frappe bash`):

```bash
bench --site emc.localhost backup                       # always back up first
# the app is already in apps/; on another bench use: bench get-app <git-url>
bench --site emc.localhost install-app telegram_access_manager
bench build --app telegram_access_manager
```

Then restart the web and worker processes so they can import the new app. From the host:

```bash
fm restart emc.localhost --supervisor
```

> **Note.** On this bench, `bench new-app` rewrote `sites/apps.txt` and dropped `fleet_logistics`
> (an app without a git repository). It has been restored. If you ever run `bench new-app` or
> `bench get-app` again, check `sites/apps.txt` afterwards.

## Secrets configuration

The bot token and webhook secret are used **only on the server** and resolved in this order:

1. **Environment variables** `TELEGRAM_BOT_TOKEN` / `TELEGRAM_WEBHOOK_SECRET`. Optional deployment
   override (e.g. Dokploy secrets); if set, they win.
2. **Telegram Access Settings → Bot Credentials** (recommended). These are Frappe *Password* fields,
   encrypted with the site's `encryption_key`. The browser and the change history only ever see
   `*****`. Leave a field untouched to keep its value. Use **Telegram → Generate Webhook Secret** for
   the secret.
3. `site_config.json` keys `telegram_bot_token` / `telegram_webhook_secret`
   (`bench --site <site> telegram-set-secrets`, hidden prompt).

The status table in Settings shows which source is in use. The token is never in source code,
fixtures or exports.

**Switching to another bot.** Paste the new token in Settings and save. The app verifies it with
Telegram, updates Bot Username/ID, and resets the poller position (update numbers are per bot;
duplicate tracking is per bot too). Then restart the poller (or click **Set Webhook**), make the new
bot an admin in every chat (**Validate Bot Access**), and ask employees to press Start in the new
bot so it can message them. Revoke the old token in @BotFather.

Keep the site's `encryption_key` (in `site_config.json`) backed up with the database. Without it,
encrypted credentials can't be decrypted after a restore; re-enter the token if that happens.

## Telegram setup

1. **Create the bot.** In Telegram, open @BotFather and run `/newbot`. Store the token as described above.
   Optional: `/setdescription` and `/setcommands` with:
   ```
   start - Privacy notice and options
   request - Request access
   status - Status of your request and access
   access - Get new invitation links
   help - Help
   ```
2. **Add the bot to each managed group/channel as an administrator** with at least:
   - **Invite users via link** (`can_invite_users`). Required to create links and to approve or decline join requests.
   - **Ban users** (`can_restrict_members`). Required for automatic removal on revocation.
   In channels the bot must be an administrator. In groups it must be an administrator to receive
   `chat_member` updates and to read other users' membership.
3. **Configure ERPNext.** Open *Telegram Access Settings* and:
   - set **Public Base URL** (`https://erp.example.com`);
   - click **Telegram → Test Connection**;
   - tick **Enabled**, keep **Update Mode = Webhook** and save;
   - click **Telegram → Set Webhook**. This registers
     `https://<base>/api/method/telegram_access_manager.api.webhook.telegram_webhook` with the secret
     token and `allowed_updates = message, channel_post, callback_query, chat_join_request, chat_member, my_chat_member`;
   - use **Webhook Info** to confirm `pending_update_count` and `last_error_message`.
4. **Register chats.** The Bot API can't list a community's chats, so each group/channel is
   registered on its own. Every registered chat is linked to the **Default Telegram Community**
   (Settings → *Chat Registration*; select an active *Telegram Community*) and stays **inactive /
   Pending Review** until an ERPNext manager activates it. Registration never grants access.
   - **Groups and supergroups: `/register`.** A chat creator or administrator sends `/register` (or
     `/register@<bot_username>`) in the group. The bot takes the chat ID, type and title from the
     update itself (arguments are ignored, so nobody can name a different chat), confirms with
     `getChatMember` that the sender is `creator`/`administrator`, and confirms that the bot is an
     admin with *Invite users via link*. Anonymous-admin messages are refused because the person
     can't be identified; turn off *Remain anonymous* for that one command.
   - **Channels: `/register CODE`.** Telegram channel posts don't identify their author (`from` is
     empty; the signature is only display text), so a plain `/register` in a channel is refused. The
     administrator sends `/register` to the bot **privately** and receives a single-use code valid for
     10 minutes. They then post `/register CODE` in the channel. The bot verifies with `getChatMember`
     that the code's owner administers that channel, replies privately, and deletes the command post
     if it has the *Delete messages* right. A code is useless to non-admins and can't be reused.
   - **Bot added/promoted.** With *Record Chats When the Bot Is Added* enabled, adding or promoting
     the bot also creates an inactive chat (status *Discovered*) under the default Community.
   - **Manual.** Create a *Telegram Chat* yourself with the numeric ID (supergroups/channels start
     with `-100`).
   - **Review** under the workspace shortcut **Chats Awaiting Review**. Check the Community, click
     **Validate Bot Access**, then tick **Active** and save (status becomes *Registered*), or use
     **Reject Registration** (later `/register` attempts are refused until a manager activates the
     chat). A chat that already belongs to a different Community is never moved by `/register`;
     change its Community in ERPNext if that's intended. Re-running `/register` refreshes the
     Telegram title and metadata only; the ERPNext chat name, active flag, access method and role
     assignments are left untouched.
5. **Create roles.** Create *Telegram Access Roles* (e.g. HR Team, Production Team, Management,
   Finance, All Employees) and add the chats.
6. **Share the bot link** (`https://t.me/<bot_username>`) with employees through HR. Don't share
   chat-folder (`t.me/addlist/...`) or community links. They would let people join every chat in the
   folder and bypass these permissions.

## ERPNext roles

| ERPNext role | Can |
|---|---|
| Telegram Access Verifier | Read requests/chats/roles/access records; match an Employee and **verify identity**. Can't approve, reject, revoke, configure, or read invite links. |
| Telegram Access Manager | Everything above, plus configure chats, roles and settings; **approve, reject, cancel**, apply changes, retry, revoke and restore access. |
| System Manager | Full access. |

Assign them in *User → Roles*. All actions are checked on the server (role check plus document
permission); hiding a button isn't relied on. Optionally enable **Require Different Users to Verify
and Approve** (four-eyes principle).

## Telegram Access portal (frappe-ui)

Everyday work happens in the portal at **`/telegram-access`**: the *Telegram Access* tile on the
apps screen, or `https://<site>/telegram-access`. It's built with [frappe-ui](https://github.com/frappe/frappe-ui)
(Vue 3 + Tailwind) and shows only what people need:

| Page | What you do there |
|---|---|
| Home | Counters, requests waiting, chats to review, recent activity |
| Requests | Link the employee, pick a role, switch chats on/off, **Approve** / Reject |
| People | See who has access and their real membership (Joined, Invite sent, …); change chats (**Save & apply**), revoke or restore |
| Groups & Channels | Review chats registered with `/register`: **Check bot**, **Activate**, **Reject** |
| Access Roles | Create roles and pick their chats; **Apply to Telegram** after changes |
| Settings | Bot token, on/off, Polling/Webhook, **Connect webhook**, default community, employee messages |

Each chat is a simple switch. Behind the scenes a switched-off role chat is an individual *Deny*
and a switched-on extra chat is an individual *Allow*, so the permission rules are unchanged.
Technical details (sync attempts, retry times, bot rights, audit log, invite links, bot updates,
advanced settings) stay in the ERPNext desk under **Advanced (ERPNext desk)**. The portal is open
to users with the *Telegram Access Manager* or *Telegram Access Verifier* role; all checks run on
the server (`api/portal.py`).

**Building.** `bench build --app telegram_access_manager` runs the app's `yarn build`, which installs
`frontend/` dependencies and writes `telegram_access_manager/public/frontend/` and
`telegram_access_manager/www/telegram-access.html` (both generated, git-ignored). For live
development: `cd apps/telegram_access_manager/frontend && yarn dev` (proxies API calls to the bench).
frappe-ui is pinned to 0.1.277, the same version as `event_planner`.

## Day-to-day administration

The **Telegram Access Management** workspace has shortcuts for Pending Access Requests, Approved
Employees, Telegram Communities, Telegram Groups & Channels, Telegram Access Roles, Access Audit Logs
and Integration Settings.

- **Request.** Select Employee → **Verify Identity** → select role, adjust chat rows (Override
  Allow/Deny) → check the preview → **Approve**. Use **Reject**/**Cancel Request** with a reason.
  **Resend Notification** appears if the private message failed (e.g. the employee blocked the bot).
- **Employee Telegram Access.** Edit role and overrides, save, review, then **Apply to Telegram**.
  **Telegram → Retry Failed**, **Refresh Membership** (re-reads actual membership for all chats) and
  **Revoke Access** (reason, optional notification).
- **Employees leaving.** When an Employee's status changes from *Active* (or a daily check finds an
  inactive one), the access record is flagged **Review Required** and an audit entry is written. A
  manager then clicks **Revoke Access**. Enable *Automatically Revoke When Employee Leaves* to make
  this automatic.
- **Failures.** Transient errors (network, HTTP 5xx, 429 flood control) are retried automatically
  every 5 minutes with exponential backoff (base 2 min, cap 60 min, honoring Telegram's
  `retry_after`), up to *Max Automatic Sync Attempts*. Permanent errors (chat not found, missing
  rights, the user is a chat admin) become **Action Required** with the error shown on the row. After
  fixing the cause, click **Retry Failed**. Re-running a sync is safe: it re-checks Telegram and only
  does what's missing.
- **Employee commands.** `/status` shows only the sender's own data. `/access` sends fresh links for
  allowed chats the employee hasn't joined (rate-limited to once per 10 minutes).

## Local development

`emc.localhost` isn't reachable by Telegram, so use one of these:

**Polling (simplest).** Telegram refuses `getUpdates` while a webhook is set, and the command checks this.

1. In Settings click **Delete Webhook**, set **Update Mode = Polling**, tick **Enabled** and save.
2. In the bench container run `bench --site emc.localhost telegram-poll` (`--once` for a single batch).
   A Redis lock prevents two pollers. Background jobs (sync, notifications) still need the worker
   container (Frappe Manager runs it).
3. Before going back to webhooks, stop the poller, set Update Mode = Webhook and click **Set Webhook**.

**Webhook through an HTTPS tunnel.** Expose the site with e.g. `cloudflared tunnel --url http://emc.localhost`.
Set Public Base URL to the tunnel URL and click **Set Webhook**. Make sure the tunnel forwards the
`Host: emc.localhost` header (or add the tunnel host to the site's host mapping).

Never run the poller and a webhook for the same bot at the same time. Use a separate test bot for development.

## Production deployment with Dokploy

1. Add this app to the image you deploy (e.g. a `frappe_docker` custom image with an `apps.json`
   that includes the app's git repository, then build and push).
2. In Dokploy → your ERPNext compose app → **Environment**, add `TELEGRAM_BOT_TOKEN` and
   `TELEGRAM_WEBHOOK_SECRET`. Make sure the variables reach **every** service that runs app code:
   `backend`, `queue-short`, `queue-long` and `scheduler` (e.g. via a shared `x-environment`/`env_file`
   in the compose file). Keep them out of the git repository.
3. Deploy, then run in the backend container:
   ```bash
   bench --site <site> install-app telegram_access_manager   # first time
   bench --site <site> migrate                               # on upgrades
   ```
4. Make sure the site is served over **HTTPS** (Dokploy/Traefik certificate) and that
   `/api/method/telegram_access_manager.api.webhook.telegram_webhook` is reachable from the Internet.
   Telegram supports ports 443, 80, 88 and 8443.
5. In *Telegram Access Settings*, set Public Base URL, Test Connection, Enable, Set Webhook.
6. Make sure the scheduler is enabled (`bench --site <site> scheduler status`) for retries, link
   expiry and the daily employee review.

## Troubleshooting

| Symptom | Check |
|---|---|
| Settings shows token/secret *Missing* | The variable isn't visible to that process. Restart the web/worker containers after setting environment variables; with `site_config.json`, run `telegram-set-secrets` on the correct site. |
| *Webhook Info* `last_error_message` "Wrong response from the webhook: 403" | Secret mismatch. Click **Set Webhook** again after changing the secret. 403 also means the secret isn't configured on the server. |
| "SSL error" / "Connection refused" in Webhook Info | The site isn't publicly reachable over valid HTTPS, or the reverse proxy doesn't route the site host. |
| Bot doesn't answer | Settings *Enabled* off, wrong Update Mode, worker not running, or check *Telegram Bot Update* (status *Failed* shows the error) and the Error Log. |
| `telegram-poll`: "A webhook is set" / 409 Conflict | Delete the webhook first; make sure no other poller or deployment uses the same bot. |
| Chat validation: "chat not found" | Wrong ID (missing `-100` prefix?), the bot isn't in the chat, or a group was upgraded to a supergroup (new ID). Migrations are followed automatically if the bot sees the migration message; otherwise update the ID. |
| "not enough rights" / bot status *Member* | Promote the bot to admin with *Invite users via link* (and *Ban users*). Then **Validate Bot Access** and **Retry Failed**. |
| Row *Action Required*: "User is an administrator/owner" | Bots can't remove chat admins/owners. Remove them manually in Telegram. |
| Employee says the link doesn't work | Links expire after *Invite Link Expiry (Hours)*. The employee sends `/access` for new ones, or a manager uses **Apply to Telegram**. Links only work for the approved account; others are declined. |
| Notification *Failed* | The employee blocked the bot or never pressed Start. Ask them to open the bot, then **Resend Notification**. |
| `/register` says "not configured yet" | Set an **active** Default Telegram Community in Settings. |
| `/register` does nothing in a group | Another bot was addressed (`/register@OtherBot`), the poller/webhook runs old code (restart it), or the per-user rate limit was hit. |
| `/register` in a channel does nothing | `channel_post` isn't in the bot's allowed updates. Restart the poller, or click **Set Webhook** again after upgrading. |
| Join request neither approved nor declined | The chat isn't registered/active in ERPNext (unmanaged chats are left to human admins), or *Decline Unauthorized Join Requests* is off. |

## Telegram limitations and manual steps

Checked against the official Bot API documentation (Bot API 10.3):

- **Bots can't add users to chats directly.** Access always goes through a personal invite link,
  which the employee must open.
- **Join requests.** Links are created with `creates_join_request=true`, and the bot approves or
  declines each request with `approveChatJoinRequest` / `declineChatJoinRequest` (needs
  `can_invite_users`). If Telegram includes a `query_id` (the bot is assigned to process the chat's
  join requests), the bot must answer within **10 seconds** with `answerChatJoinRequestQuery`. These
  updates are processed directly in the webhook request rather than queued. Unauthorized requests are
  answered `decline` (or `queue` if declining is disabled).
- **Basic groups** use single-use (`member_limit=1`) links instead of join requests, and removed users
  can't be unbanned through the API (unban is for supergroups/channels). We recommend converting
  groups to supergroups. Telegram assigns a new chat ID, which the app follows when it sees the
  migration message.
- **Removal** is `banChatMember` followed by `unbanChatMember(only_if_banned)` (a "kick"), unless
  *Keep Removed Users Banned* is set. Chat owners and administrators can't be removed by the bot.
- **Membership reads** (`getChatMember`) are only guaranteed when the bot is an administrator.
- **Communities / folders.** The Bot API can't enumerate the chats of a community or folder, so
  chats are registered individually. Community/folder links are informational and must not be shared.
- **Existing members.** People who joined before the app was introduced aren't removed automatically.
  Create access records for them through the normal request flow, then use **Refresh Membership**,
  and review the remaining members in Telegram manually.
- **Private messages.** The bot can only message employees who have started it.

## Security notes

- The only guest endpoint is the webhook. It accepts POST only, is IP rate-limited (1200/min), limits
  the body to 1 MB, and compares `X-Telegram-Bot-Api-Secret-Token` in constant time. It refuses
  everything (403) while no secret is configured. Payloads are deduplicated by `update_id` and handled
  in background jobs. The handlers only act for the sending Telegram user.
- Per-user abuse protection: a message rate limit (default 20/min), a daily request limit
  (default 3), and a `/access` cooldown.
- Admin API methods and document actions check Frappe roles on the server. Request and access
  actions reload the saved document, so unsaved client values are ignored. Effective permissions are
  always recomputed from the database. Status, verification, identity and sync fields can only be
  changed by the workflow actions.
- Bot tokens never appear in exceptions (`requests` errors that contain the URL are suppressed).
  Audit and error logs are redacted (tokens and `t.me/+…` links). Invite links are encrypted at rest
  and visible to no UI role.
- Use HTTPS in production.

## Tests

The tests mock all Telegram calls (`FakeTelegram`). No network access, real bot or real messages
are used. They create their own fixtures and roll everything back.

```bash
bench --site emc.localhost set-config allow_tests true    # tests are disabled by default
bench --site emc.localhost run-tests --app telegram_access_manager
bench --site emc.localhost set-config allow_tests false   # optional
```

Coverage: request creation and duplicate updates, identity matching/conflicts, verification and
approval permissions, role-based access, Allow/Deny overrides, rejection/cancellation, revocation and
removal, role/chat recalculation without premature Telegram changes, unauthorized API calls, webhook
secret validation, missing/invalid configuration, Telegram API failures with bounded retries, join
request handling (including `query_id`), unauthorized joins, and the guarantee that approving one
chat never grants another.
