// Shared UI helpers for Telegram Access Manager forms. No secrets ever reach the browser.
frappe.provide("tam");

tam.MANAGER_ROLES = ["Telegram Access Manager", "System Manager"];
tam.VERIFIER_ROLES = ["Telegram Access Manager", "Telegram Access Verifier", "System Manager"];
tam.is_manager = () => frappe.user.has_role(tam.MANAGER_ROLES);
tam.is_verifier = () => frappe.user.has_role(tam.VERIFIER_ROLES);

const COLORS = {
	Allow: "green",
	Deny: "red",
	Member: "green",
	"Chat Admin": "blue",
	"Invite Sent": "orange",
	"Join Requested": "orange",
	Removed: "gray",
	Banned: "red",
	"Not a Member": "gray",
	Left: "gray",
	Unknown: "gray",
	Error: "red",
	Synced: "green",
	Pending: "orange",
	Failed: "red",
	"Action Required": "red",
};

const pill = (value) =>
	value
		? `<span class="indicator-pill ${COLORS[value] || "gray"}">${frappe.utils.escape_html(__(value))}</span>`
		: "";

tam.render_permissions = (field, rows, opts = {}) => {
	const $wrapper = field && field.$wrapper;
	if (!$wrapper) return;
	if (!rows || !rows.length) {
		$wrapper.html(
			`<div class="text-muted small">${__(
				"No chats yet. Select a Telegram Access Role or add chats with Override = Allow."
			)}</div>`
		);
		return;
	}
	const esc = frappe.utils.escape_html;
	const allowed = rows.filter((r) => r.effective === "Allow").length;
	const head = [__("Chat"), __("Type"), __("Source"), __("Override"), __("Effective"), __("Note")];
	if (opts.with_state) head.push(__("Actual Membership"), __("Sync"));
	const body = rows
		.map((r) => {
			const cells = [
				`<a href="/app/telegram-chat/${encodeURIComponent(r.telegram_chat)}">${esc(r.telegram_chat)}</a>`,
				esc(r.chat_type || ""),
				esc(__(r.source || "")),
				esc(__(r.override || "Inherit")),
				pill(r.effective),
				`<span class="text-muted">${esc(r.note || "")}</span>`,
			];
			if (opts.with_state) {
				cells.push(pill(r.membership_status));
				cells.push(
					pill(r.sync_status) +
						(r.last_error ? `<div class="small text-danger">${esc(r.last_error)}</div>` : "")
				);
			}
			return `<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`;
		})
		.join("");
	$wrapper.html(`
		<div class="mb-2"><b>${__("Effective permissions")}</b>:
			${__("{0} of {1} chats allowed", [allowed, rows.length])}
			<span class="text-muted small"> — ${__(
				"Individual Deny overrides the role; individual Allow adds a chat; otherwise the role applies."
			)}</span>
		</div>
		<div class="table-responsive"><table class="table table-bordered table-sm">
			<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
			<tbody>${body}</tbody>
		</table></div>`);
};

tam.ensure_saved = async (frm) => {
	if (frm.is_new() || frm.is_dirty()) {
		await frm.save();
	}
};
