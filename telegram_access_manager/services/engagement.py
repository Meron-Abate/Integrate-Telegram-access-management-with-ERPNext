"""Weekly community engagement: message counting, statistics and the Monday / Wednesday / Friday posts.

How departments are found (nothing is configured by hand; role membership is the source of truth):

- A *department group* is an active, registered Telegram group or supergroup that is assigned to
  exactly one "<Name> Team" Telegram Access Role. Chats shared by several teams (company-wide groups)
  and channels are not department groups, so they are left out of every campaign.
- The group's Head(s) are the active "<Name> Head" roles assigned to the same chat. Heads are only used
  for the Wednesday mention; they also count as members of the department.
- Eligible members are active Employee Telegram Access records with an effective "Allow" on the chat
  whose access role is the team role or a head role of that department. Roles listed in
  "Excluded Access Roles" (HR, Managers, Admin by default) are never counted.

Messages are recorded by the existing webhook (handlers.handle_group_message); only metadata is stored,
never message text. All dates are Africa/Addis_Ababa dates, whatever the server time zone is.
"""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import cint, escape_html, now_datetime

from telegram_access_manager import telegram as tg
from telegram_access_manager.constants import (
	DT_ACCESS,
	DT_ACTIVITY,
	DT_CHAT,
	DT_ENGAGEMENT_LOG,
	DT_ROLE,
	SETTINGS,
)
from telegram_access_manager.services.audit import log_error
from telegram_access_manager.telegram.client import TelegramAPIError, redact

TIMEZONE = ZoneInfo("Africa/Addis_Ababa")
TEAM_SUFFIX = " team"
HEAD_SUFFIX = " head"

# campaign -> (weekday, local start time). Monday is 0.
CAMPAIGNS = {"Monday": (0, time(9, 0)), "Wednesday": (2, time(10, 0)), "Friday": (4, time(16, 0))}
CATCH_UP = timedelta(
	hours=3
)  # a missed run (server down, Telegram error) is retried until this long after the start
MAX_ATTEMPTS = 3
SENDING_STALE = timedelta(minutes=10)  # a "Sending" log older than this belongs to a crashed run
RETENTION_DAYS = 180

DEFAULT_TARGET_PCT = 50
TOP_N = 3
DEFAULT_EXCLUDED_ROLES = ("HR", "Managers", "Admin")
INACTIVE_MEMBERSHIP = ("Left", "Removed", "Banned")

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
# A message counts when it carries something a person wrote or shared. Everything else
# (joins, pins, topic changes, title changes, ...) is a Telegram service message.
CONTENT_TYPES = (
	"text",
	"photo",
	"video",
	"document",
	"audio",
	"voice",
	"animation",
	"sticker",
	"video_note",
	"poll",
	"location",
	"contact",
)


# ------------------------------------------------------------------------------------------- time
def now_addis() -> datetime:
	return datetime.now(TIMEZONE)


def week_start_of(day: date) -> date:
	return day - timedelta(days=day.weekday())


def settings():
	return frappe.get_cached_doc(SETTINGS)


def is_enabled() -> bool:
	s = settings()
	return bool(s.enabled and cint(s.engagement_enabled))


def target_pct() -> int:
	return cint(settings().engagement_participation_target) or DEFAULT_TARGET_PCT


def excluded_roles() -> set[str]:
	raw = settings().engagement_excluded_roles
	lines = (raw if raw is not None else "\n".join(DEFAULT_EXCLUDED_ROLES)).splitlines()
	return {line.strip().lower() for line in lines if line.strip()}


# ------------------------------------------------------------------------------------ departments
def classify_role(role_name: str) -> tuple[str | None, str]:
	"""("team" | "head" | None, department name) from a role name such as "Motion Team"."""
	lowered = (role_name or "").strip().lower()
	for kind, suffix in (("team", TEAM_SUFFIX), ("head", HEAD_SUFFIX)):
		if lowered.endswith(suffix):
			return kind, role_name.strip()[: -len(suffix)].strip().lower()
	return None, ""


def find_departments(chat: str | None = None) -> list[dict]:
	"""Department groups, optionally only the one for ``chat`` (a Telegram Chat name or numeric chat ID)."""
	filters = {
		"is_active": 1,
		"registration_status": "Registered",
		"chat_type": ["in", ["Group", "Supergroup"]],
	}
	if chat:
		filters["name" if frappe.db.exists(DT_CHAT, chat) else "telegram_chat_id"] = str(chat)
	chats = {
		c.name: c
		for c in frappe.get_all(
			DT_CHAT, filters=filters, fields=["name", "chat_name", "telegram_chat_id", "telegram_title"]
		)
	}
	if not chats:
		return []

	active_roles = set(frappe.get_all(DT_ROLE, filters={"is_active": 1}, pluck="name"))
	rows = frappe.get_all(
		"Telegram Access Role Chat",
		filters={"parenttype": DT_ROLE, "telegram_chat": ["in", list(chats)]},
		fields=["parent", "telegram_chat"],
	)
	excluded = excluded_roles()
	roles_by_chat: dict[str, list[str]] = {}
	for row in rows:
		if row.parent in active_roles and row.parent.lower() not in excluded:
			roles_by_chat.setdefault(row.telegram_chat, []).append(row.parent)

	departments = []
	for name, roles in roles_by_chat.items():
		teams = [r for r in roles if classify_role(r)[0] == "team"]
		if len(teams) != 1:
			continue  # no team, or a chat shared by several teams: not a department group
		department = classify_role(teams[0])[1]
		heads = sorted(r for r in roles if classify_role(r) == ("head", department))
		c = chats[name]
		departments.append(
			{
				"chat": name,
				"chat_id": c.telegram_chat_id,
				"name": teams[0],
				"title": c.telegram_title or c.chat_name,
				"team_role": teams[0],
				"head_roles": heads,
			}
		)
	return sorted(departments, key=lambda d: d["name"].lower())


def get_members(dept: dict) -> list[dict]:
	"""Eligible members of a department: one row per employee, excluded roles removed."""
	roles = {dept["team_role"], *dept["head_roles"]}
	rows = frappe.db.sql(
		"""
		select a.name as access, a.employee, a.employee_name, a.telegram_user_id, a.telegram_username, a.access_role
		from `tabEmployee Telegram Access` a
		join `tabEmployee Telegram Chat Permission` p on p.parent = a.name and p.parenttype = %(dt)s
		where a.is_active = 1 and p.telegram_chat = %(chat)s and p.effective_permission = 'Allow'
			and ifnull(p.membership_status, '') not in %(inactive)s
		""",
		{"dt": DT_ACCESS, "chat": dept["chat"], "inactive": INACTIVE_MEMBERSHIP},
		as_dict=True,
	)
	excluded = excluded_roles()
	return [
		r
		for r in rows
		if r.access_role in roles and r.access_role.lower() not in excluded and r.telegram_user_id
	]


def get_heads(dept: dict, members: list[dict] | None = None) -> list[dict]:
	members = get_members(dept) if members is None else members
	return [m for m in members if m.access_role in dept["head_roles"]]


# -------------------------------------------------------------------------------- recording
def message_type_of(message: dict) -> str | None:
	"""The kind of a user message, or None for Telegram service messages."""
	return next((t for t in CONTENT_TYPES if t in message), None)


def record_message(message: dict) -> str:
	"""Count one group message. Returns "recorded", "duplicate" or "ignored"; never stores message text."""
	if not is_enabled():
		return "ignored"
	chat = message.get("chat") or {}
	user = message.get("from") or {}
	message_id = message.get("message_id")
	if chat.get("type") not in ("group", "supergroup") or not user.get("id") or user.get("is_bot"):
		return "ignored"
	if message.get("sender_chat") or message.get("is_automatic_forward") or not isinstance(message_id, int):
		return "ignored"
	message_type = message_type_of(message)
	if not message_type:
		return "ignored"

	chat_id, user_id = str(chat["id"]), str(user["id"])
	if not find_departments(chat_id):
		return "ignored"
	if not frappe.db.get_value(DT_ACCESS, {"telegram_user_id": user_id, "is_active": 1}, "name"):
		return "ignored"  # not a known employee: never invent an identity

	sent = datetime.fromtimestamp(cint(message.get("date")) or datetime.now().timestamp(), TIMEZONE)
	# Replies inside a normal (non-forum) group also carry message_thread_id; only forum topics are topics.
	thread_id = message.get("message_thread_id") if message.get("is_topic_message") else None
	try:
		frappe.get_doc(
			{
				"doctype": DT_ACTIVITY,
				"activity_key": f"{chat_id}:{message_id}",
				"telegram_chat_id": chat_id,
				"telegram_user_id": user_id,
				"message_id": message_id,
				"message_thread_id": thread_id,
				"message_type": message_type,
				"message_on": sent.replace(tzinfo=None),
				"activity_date": sent.date(),
			}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		return "duplicate"
	return "recorded"


def purge_old_activity():
	cutoff = now_addis().date() - timedelta(days=RETENTION_DAYS)
	frappe.db.delete(DT_ACTIVITY, {"activity_date": ["<", cutoff]})


# ----------------------------------------------------------------------------------------- stats
def compute_stats(dept: dict, start: date, end: date) -> dict:
	"""Participation of a department between two dates (inclusive). Message counts are never capped."""
	members = get_members(dept)
	by_user = {m.telegram_user_id: m for m in members}
	rows = frappe.db.sql(
		"""
		select telegram_user_id, activity_date, count(*) as messages
		from `tabTelegram Message Activity`
		where telegram_chat_id = %s and activity_date between %s and %s
		group by telegram_user_id, activity_date
		""",
		(dept["chat_id"], start, end),
		as_dict=True,
	)
	per_person: dict[str, dict] = {}
	days = set()
	for r in rows:
		member = by_user.get(r.telegram_user_id)
		if not member:
			continue  # unmatched, excluded role, or no longer eligible
		p = per_person.setdefault(
			r.telegram_user_id,
			{
				"employee": member.employee,
				"employee_name": member.employee_name or member.employee,
				"telegram_user_id": r.telegram_user_id,
				"messages": 0,
				"active_days": 0,
			},
		)
		p["messages"] += r.messages
		p["active_days"] += 1
		days.add(r.activity_date)

	people = sorted(
		per_person.values(), key=lambda p: (-p["messages"], -p["active_days"], p["employee_name"].lower())
	)
	eligible, active = len(members), len(people)
	consistent = [p for p in people if p["active_days"] >= 2]
	return {
		"chat": dept["chat"],
		"department": dept["name"],
		"title": dept["title"],
		"start": start,
		"end": end,
		"eligible": eligible,
		"active": active,
		"participation_pct": round(100 * active / eligible) if eligible else 0,
		"total_messages": sum(p["messages"] for p in people),
		"active_days": len(days),
		"consistent": len(consistent),
		"consistent_pct": round(100 * len(consistent) / eligible) if eligible else 0,
		"most_active": people,
		"most_consistent": sorted(
			consistent, key=lambda p: (-p["active_days"], -p["messages"], p["employee_name"].lower())
		),
	}


def rank_key(stats: dict) -> tuple:
	"""Overall ranking: coverage first, then consistency, then volume."""
	return (stats["participation_pct"], stats["consistent_pct"], stats["total_messages"])


def pick_winner(all_stats: list[dict]) -> dict | None:
	candidates = [s for s in all_stats if s["eligible"] and s["total_messages"]]
	return max(candidates, key=rank_key) if candidates else None


# ---------------------------------------------------------------------------------------- texts
def mention(member: dict) -> str:
	username = (member.get("telegram_username") or "").lstrip("@")
	if USERNAME_RE.match(username):
		return f"@{username}"
	name = escape_html(member.get("employee_name") or member.get("employee") or "Team Lead")
	return f'<a href="tg://user?id={int(member["telegram_user_id"])}">{name}</a>'


def _join(items: list[str]) -> str:
	return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def monday_text() -> str:
	return (
		"Happy Monday, team! 🎉\n\n"
		"New week, fresh energy and another chance to get things done (and hopefully have some fun along the way 😄).\n\n"
		"Share your updates, ask questions, drop ideas and help each other out. "
		"Let's keep the group alive and the good teamwork going!\n\n"
		"Let's make it a great week.\n\n"
		"#EternalCommunity"
	)


def wednesday_text(dept: dict, stats: dict, heads: list[dict]) -> str:
	team = escape_html(dept["name"])
	leads = _join([mention(h) for h in heads]) if heads else "Team Leads"
	if stats["participation_pct"] >= target_pct():
		return (
			f"🌟 Hey, {team}!\n\n"
			f"Midweek check-in: {stats['active']} of {stats['eligible']} teammates have already joined the conversation "
			"this week. That's great energy!\n\n"
			f"{leads}, thank you for keeping the team connected. 👏\n\n"
			"Keep sharing updates, questions and ideas. 🚀"
		)
	return (
		f"👀 Hey, {team}!\n\n"
		"How is everyone doing this week?\n\n"
		"We've noticed that participation in this group could use a little boost.\n\n"
		f"{leads}, we need your leadership here! Encourage the team to share updates, discuss ongoing work "
		"and keep everyone connected.\n\n"
		"Don't let your team's communication channel become a ghost town. 😂\n\n"
		"We know you can do better! Let's get everyone involved! 🚀"
	)


def friday_text(dept: dict, stats: dict, winner: dict | None) -> str:
	team = escape_html(dept["name"])
	lines = ["🏆 <b>ETERNAL WEEKLY COMMUNITY AWARDS</b>", ""]
	if not stats["total_messages"]:
		lines += [
			f"It was a quiet week in the {team} group. 🤫",
			"",
			"Let's bring the conversation back next week: share an update, ask a question or drop an idea!",
		]
	else:
		lines += ["What a week! 🎉", "", "🌟 <b>Most Active Members:</b>"]
		lines += [
			f"• {escape_html(p['employee_name'])} – {p['messages']} {'message' if p['messages'] == 1 else 'messages'}"  # noqa: RUF001
			for p in stats["most_active"][:TOP_N]
		]
		if stats["most_consistent"]:
			lines += ["", "🔥 <b>Most Consistent Members:</b>"]
			lines += [
				f"• {escape_html(p['employee_name'])} – active on {p['active_days']} different days"  # noqa: RUF001
				for p in stats["most_consistent"][:TOP_N]
			]
		if stats["participation_pct"] >= target_pct():
			lines += [
				"",
				f"👏 Great teamwork, {team}! {stats['active']} of {stats['eligible']} teammates joined the conversation this week.",
			]
	if winner:
		lines += ["", "🏅 <b>Most Active Team:</b>"]
		if winner["chat"] == stats["chat"]:
			lines.append(f"Congratulations to the {team} for keeping the conversations flowing this week!")
		else:
			lines.append(f"{escape_html(winner['department'])} led the way this week. Congratulations!")
	lines += [
		"",
		"Special appreciation to everyone sharing updates, asking questions and helping colleagues.",
		"",
		"Next week, we want to see even more departments shining. 🚀",
	]
	return "\n".join(lines)


def build_text(
	campaign: str, dept: dict, start: date, end: date, winner_stats: list[dict] | None = None
) -> str | None:
	"""The message for one department, or None when there is nothing sensible to say."""
	if campaign == "Monday":
		return monday_text()
	stats = compute_stats(dept, start, end)
	if not stats["eligible"]:
		return None
	if campaign == "Wednesday":
		return wednesday_text(dept, stats, get_heads(dept))
	return friday_text(dept, stats, pick_winner(winner_stats or [stats]))


# ----------------------------------------------------------------------------------- campaigns
def _claim(key: str, campaign: str, dept: dict, start: date, force: bool):
	"""Reserve a campaign/week/group. Returns the log name to work on, or None when it must not be sent now."""
	existing = frappe.db.get_value(DT_ENGAGEMENT_LOG, key, ["status", "attempts", "modified"], as_dict=True)
	if not existing:
		try:
			frappe.get_doc(
				{
					"doctype": DT_ENGAGEMENT_LOG,
					"log_key": key,
					"campaign": campaign,
					"telegram_chat": dept["chat"],
					"telegram_chat_id": dept["chat_id"],
					"week_start": start,
					"status": "Sending",
					"attempts": 1,
				}
			).insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			return None  # another worker claimed it first
		return key
	if not force:
		if existing.status in ("Sent", "Skipped"):
			return None
		if existing.status == "Failed" and existing.attempts >= MAX_ATTEMPTS:
			return None
		if existing.status == "Sending" and now_datetime() - existing.modified < SENDING_STALE:
			return None
	frappe.db.set_value(
		DT_ENGAGEMENT_LOG, key, {"status": "Sending", "attempts": cint(existing.attempts) + 1, "error": None}
	)
	return key


def _commit():
	if not frappe.flags.in_test:
		frappe.db.commit()


def run_campaign(
	campaign: str, now: datetime | None = None, chat: str | None = None, force: bool = False
) -> list[dict]:
	"""Post a campaign to every department group (or just ``chat``). Safe to call repeatedly.

	``force`` re-sends even when this week's post already went out (for testing with ``bench execute``).
	Telegram failures are logged on the Telegram Engagement Log and never raised.
	"""
	if campaign not in CAMPAIGNS:
		frappe.throw(f"Unknown campaign {campaign}")
	today = (now or now_addis()).date()
	start = week_start_of(today)
	departments = find_departments()
	targets = [d for d in departments if not chat or chat in (d["chat"], d["chat_id"])]
	winner_stats = [compute_stats(d, start, today) for d in departments] if campaign == "Friday" else None

	results = []
	sent_chat_ids = set()
	for dept in targets:
		if dept["chat_id"] in sent_chat_ids:
			continue  # never message the same Telegram chat twice in one run
		sent_chat_ids.add(dept["chat_id"])
		key = f"{campaign}:{start.isoformat()}:{dept['chat_id']}"
		result = {"chat": dept["chat"], "department": dept["name"], "status": "Already handled"}
		results.append(result)

		if not _claim(key, campaign, dept, start, force):
			continue
		text = None
		try:
			text = build_text(campaign, dept, start, today, winner_stats)
			if text is None:
				frappe.db.set_value(
					DT_ENGAGEMENT_LOG, key, {"status": "Skipped", "error": "No eligible members"}
				)
				result["status"] = "Skipped"
				_commit()
				continue
			sent = tg.get_client().send_message(dept["chat_id"], text)
			frappe.db.set_value(
				DT_ENGAGEMENT_LOG,
				key,
				{
					"status": "Sent",
					"sent_on": now_datetime(),
					"message": text,
					"telegram_message_id": str((sent or {}).get("message_id") or ""),
					"error": None,
				},
			)
			result["status"] = "Sent"
		except TelegramAPIError as e:
			frappe.db.set_value(
				DT_ENGAGEMENT_LOG,
				key,
				{"status": "Failed", "message": text, "error": redact(e.description)[:500]},
			)
			result.update(status="Failed", error=e.description)
			log_error(f"Telegram engagement {campaign} failed", f"{dept['chat']}: {e}")
		except Exception:
			frappe.db.set_value(
				DT_ENGAGEMENT_LOG,
				key,
				{"status": "Failed", "message": text, "error": "Unexpected error, see Error Log"},
			)
			result.update(status="Failed", error="Unexpected error")
			log_error(f"Telegram engagement {campaign} failed", frappe.get_traceback())
		_commit()
	return results


def due_campaign(now: datetime) -> str | None:
	"""The campaign whose window (start time until start + CATCH_UP) contains ``now`` (Addis Ababa time)."""
	for name, (weekday, at) in CAMPAIGNS.items():
		if now.weekday() != weekday:
			continue
		begin = datetime.combine(now.date(), at, tzinfo=TIMEZONE)
		if begin <= now < begin + CATCH_UP:
			return name
	return None


def run_scheduled(now: datetime | None = None) -> list[dict]:
	"""Scheduler entry point (every 15 minutes). The Addis Ababa clock decides what is due, so the result
	does not depend on the server time zone; the engagement log makes every run idempotent."""
	if not is_enabled():
		return []
	now = now.astimezone(TIMEZONE) if now else now_addis()
	campaign = due_campaign(now)
	return run_campaign(campaign, now=now) if campaign else []


# --------------------------------------------------------------------------------------- dashboard
def get_dashboard(week_start: date | None = None) -> dict:
	today = now_addis().date()
	start = week_start or week_start_of(today)
	end = min(today, start + timedelta(days=6)) if start <= today else start
	departments = find_departments()
	stats = sorted((compute_stats(d, start, end) for d in departments), key=rank_key, reverse=True)
	history = frappe.get_all(
		DT_ENGAGEMENT_LOG,
		filters={"campaign": "Friday", "status": "Sent"},
		fields=["telegram_chat", "week_start", "sent_on", "message"],
		order_by="week_start desc, telegram_chat asc",
		limit=30,
	)
	groups = frappe.db.count(DT_CHAT, {"is_active": 1, "chat_type": ["in", ["Group", "Supergroup"]]}) - len(
		departments
	)
	return {
		"enabled": is_enabled(),
		"week_start": start,
		"week_end": end,
		"target_pct": target_pct(),
		"departments": stats,
		"history": history,
		"other_groups": max(groups, 0),
	}


def preview(campaign: str, chat: str | None = None) -> list[dict]:
	"""The messages a campaign would send right now. Sends nothing and writes nothing."""
	today = now_addis().date()
	start = week_start_of(today)
	departments = find_departments()
	winner_stats = [compute_stats(d, start, today) for d in departments] if campaign == "Friday" else None
	return [
		{
			"chat": d["chat"],
			"department": d["name"],
			"text": build_text(campaign, d, start, today, winner_stats),
		}
		for d in departments
		if not chat or chat in (d["chat"], d["chat_id"])
	]
