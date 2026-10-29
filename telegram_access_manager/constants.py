"""Shared constants for Telegram Access Manager."""

# ERPNext (Frappe) roles. These are unrelated to "Telegram Access Role" documents.
ROLE_MANAGER = "Telegram Access Manager"
ROLE_VERIFIER = "Telegram Access Verifier"
ROLE_SYSTEM_MANAGER = "System Manager"

MANAGER_ROLES = frozenset({ROLE_MANAGER, ROLE_SYSTEM_MANAGER})
VERIFIER_ROLES = frozenset({ROLE_MANAGER, ROLE_VERIFIER, ROLE_SYSTEM_MANAGER})

SETTINGS = "Telegram Access Settings"
DT_COMMUNITY = "Telegram Community"
DT_CHAT = "Telegram Chat"
DT_ROLE = "Telegram Access Role"
DT_REQUEST = "Telegram Access Request"
DT_ACCESS = "Employee Telegram Access"
DT_AUDIT = "Telegram Access Audit Log"
DT_INVITE = "Telegram Invite Link"
DT_UPDATE = "Telegram Bot Update"

# Access request statuses
REQ_PENDING_VERIFICATION = "Pending Verification"
REQ_PENDING_APPROVAL = "Pending Approval"
REQ_APPROVED = "Approved"
REQ_REJECTED = "Rejected"
REQ_CANCELLED = "Cancelled"
REQ_ERROR = "Error"
OPEN_REQUEST_STATUSES = (REQ_PENDING_VERIFICATION, REQ_PENDING_APPROVAL, REQ_ERROR)
CLOSED_REQUEST_STATUSES = (REQ_APPROVED, REQ_REJECTED, REQ_CANCELLED)

# Permission values
ALLOW = "Allow"
DENY = "Deny"
INHERIT = "Inherit"
SOURCE_ROLE = "Role"
SOURCE_INDIVIDUAL = "Individual"
SOURCE_NONE = "None"

# Chat member row: actual membership as last confirmed by Telegram
MEMBER = "Member"
CHAT_ADMIN = "Chat Admin"
NOT_A_MEMBER = "Not a Member"
INVITE_SENT = "Invite Sent"
REMOVED = "Removed"
BANNED = "Banned"
LEFT = "Left"
UNKNOWN = "Unknown"

# Row sync status
ROW_PENDING = "Pending"
ROW_SYNCED = "Synced"
ROW_FAILED = "Failed"
ROW_ACTION_REQUIRED = "Action Required"

# Access method of a chat
METHOD_JOIN_REQUEST = "Join Request"
METHOD_SINGLE_USE = "Single-Use Invite Link"

# Invite link names are prefixed so links created by this app can be recognised in updates.
INVITE_LINK_PREFIX = "TAM "

# channel_post is needed for channel registration (/register CODE). Listing update types explicitly is
# required because chat_member is only delivered when requested.
ALLOWED_UPDATES = ["message", "channel_post", "callback_query", "chat_join_request", "chat_member", "my_chat_member"]

WEBHOOK_METHOD_PATH = "/api/method/telegram_access_manager.api.webhook.telegram_webhook"
