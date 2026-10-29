frappe.ui.form.on("Employee Telegram Access", {
	setup(frm) {
		frm.set_query("access_role", () => ({ filters: { is_active: 1 } }));
		frm.set_query("telegram_chat", "chat_permissions", () => ({
			filters: {
				is_active: 1,
				name: ["not in", (frm.doc.chat_permissions || []).map((r) => r.telegram_chat).filter(Boolean)],
			},
		}));
	},

	refresh(frm) {
		const colors = { Synced: "green", "Changes Pending": "orange", Queued: "blue", "In Progress": "blue", "Partially Synced": "orange", Failed: "red" };
		frm.page.set_indicator(
			frm.doc.is_active ? __(frm.doc.sync_status || "Not Synced") : __("Revoked"),
			frm.doc.is_active ? colors[frm.doc.sync_status] || "gray" : "red"
		);
		if (frm.doc.review_required) {
			frm.set_intro(__("Review required: {0}", [frappe.utils.escape_html(frm.doc.review_reason || "")]), "red");
		} else if (frm.doc.sync_status === "Changes Pending") {
			frm.set_intro(__("Desired permissions changed. Review the preview, then use 'Apply to Telegram'."), "orange");
		}
		frm.trigger("render_preview");
		if (!tam.is_manager() || frm.is_new()) return;

		if (frm.doc.is_active) {
			frm.add_custom_button(__("Apply to Telegram"), () => run(frm, "apply_to_telegram", __("Changes queued"))).addClass("btn-primary");
			frm.add_custom_button(__("Retry Failed"), () => run(frm, "retry_failed", __("Retry queued")), __("Telegram"));
			frm.add_custom_button(__("Refresh Membership"), () => run(frm, "refresh_membership", __("Refresh queued")), __("Telegram"));
			frm.add_custom_button(__("Revoke Access"), () => revoke_dialog(frm), __("Telegram"));
		} else {
			frm.add_custom_button(__("Restore Access"), () => run(frm, "restore_access", __("Access restored; apply it to Telegram")));
			frm.add_custom_button(__("Apply Revocation Again"), () => run(frm, "apply_to_telegram", __("Queued")), __("Telegram"));
		}
	},

	access_role(frm) {
		frm.trigger("render_preview");
	},

	render_preview(frm) {
		frm.call("get_effective_permissions_preview").then((r) =>
			tam.render_permissions(frm.fields_dict.effective_preview, r.message, { with_state: true })
		);
	},
});

frappe.ui.form.on("Employee Telegram Chat Permission", {
	chat_permissions_add(frm, cdt, cdn) {
		locals[cdt][cdn].override = "Allow";
		frm.refresh_field("chat_permissions");
	},
	override(frm, cdt, cdn) {
		if (locals[cdt][cdn].telegram_chat) frm.trigger("render_preview");
	},
	telegram_chat(frm, cdt, cdn) {
		if (locals[cdt][cdn].telegram_chat) frm.trigger("render_preview");
	},
});

async function run(frm, method, message) {
	await tam.ensure_saved(frm);
	await frm.call(method);
	frappe.show_alert({ message, indicator: "green" });
	setTimeout(() => frm.reload_doc(), 1500);
}

function revoke_dialog(frm) {
	frappe.prompt(
		[
			{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
			{ fieldname: "notify", fieldtype: "Check", label: __("Notify the employee on Telegram"), default: 1 },
		],
		async (values) => {
			await tam.ensure_saved(frm);
			await frm.call("revoke_access", values);
			frappe.show_alert({ message: __("Access revoked; Telegram removal queued"), indicator: "orange" });
			setTimeout(() => frm.reload_doc(), 1500);
		},
		__("Revoke all managed Telegram access"),
		__("Revoke")
	);
}
