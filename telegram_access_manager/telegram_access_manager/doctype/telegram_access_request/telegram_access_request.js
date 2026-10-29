frappe.ui.form.on("Telegram Access Request", {
	setup(frm) {
		frm.set_query("employee", () => ({ filters: { status: "Active" } }));
		frm.set_query("access_role", () => ({ filters: { is_active: 1 } }));
		// Offer active chats that are not already in the table.
		frm.set_query("telegram_chat", "chat_permissions", () => ({
			filters: {
				is_active: 1,
				name: ["not in", (frm.doc.chat_permissions || []).map((r) => r.telegram_chat).filter(Boolean)],
			},
		}));
	},

	refresh(frm) {
		frm.trigger("set_indicator");
		frm.trigger("render_preview");
		frm.trigger("add_actions");
		const closed = ["Approved", "Rejected", "Cancelled"].includes(frm.doc.status);
		frm.set_df_property("employee", "read_only", closed);
		frm.set_df_property("access_role", "read_only", closed);
		frm.set_df_property("chat_permissions", "read_only", closed);
		if (frm.doc.status === "Pending Verification" && !frm.doc.__islocal) {
			frm.set_intro(
				__(
					"Link the Employee this Telegram account belongs to, then approve. " +
						"The Telegram username or display name alone is not proof of identity."
				),
				"orange"
			);
		}
	},

	set_indicator(frm) {
		const colors = {
			"Pending Verification": "orange",
			"Pending Approval": "blue",
			Approved: "green",
			Rejected: "red",
			Cancelled: "gray",
			Error: "red",
		};
		frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
	},

	access_role(frm) {
		frm.trigger("refresh_rows");
	},

	refresh_rows(frm) {
		if (!tam.is_verifier()) return;
		frm.call("refresh_permissions").then((r) => {
			frm.refresh_field("chat_permissions");
			tam.render_permissions(frm.fields_dict.effective_preview, r.message);
		});
	},

	render_preview(frm) {
		if (!tam.is_verifier()) return;
		if (frm.doc.__islocal && !frm.doc.access_role && !(frm.doc.chat_permissions || []).length) {
			tam.render_permissions(frm.fields_dict.effective_preview, []);
			return;
		}
		frm.call("get_effective_permissions_preview").then((r) =>
			tam.render_permissions(frm.fields_dict.effective_preview, r.message)
		);
	},

	add_actions(frm) {
		if (frm.doc.__islocal) return;
		const status = frm.doc.status;
		const open = ["Pending Verification", "Pending Approval", "Error"].includes(status);

		if (status === "Pending Verification" && tam.is_verifier() && !tam.is_manager()) {
			frm.add_custom_button(__("Mark Verified"), () => mark_verified(frm)).addClass("btn-primary");
		}
		if (open && tam.is_manager()) {
			frm.add_custom_button(__("Approve"), () => approve_dialog(frm)).addClass("btn-primary");
		}
		if (open && tam.is_manager()) {
			frm.add_custom_button(__("Reject"), () => close_dialog(frm, "reject", __("Reject Request")), __("Actions"));
			frm.add_custom_button(__("Cancel Request"), () => close_dialog(frm, "cancel_request", __("Cancel Request")), __("Actions"));
		}
		if (tam.is_verifier()) {
			frm.add_custom_button(__("Preview Effective Permissions"), () => frm.trigger("refresh_rows"), __("Actions"));
		}
		if (frm.doc.notification_status === "Failed" && !open && tam.is_manager()) {
			frm.add_custom_button(__("Resend Notification"), () =>
				frm.call("retry_notification").then(() => {
					frappe.show_alert({ message: __("Notification queued"), indicator: "green" });
					frm.reload_doc();
				}), __("Actions")
			);
		}
		if (frm.doc.employee_telegram_access) {
			frm.add_custom_button(__("Employee Telegram Access"), () =>
				frappe.set_route("Form", "Employee Telegram Access", frm.doc.employee_telegram_access)
			);
		}
	},
});

frappe.ui.form.on("Telegram Access Request Chat", {
	chat_permissions_add(frm, cdt, cdn) {
		// A manually added chat is normally an individual grant. Set it locally: refreshing now
		// would recalculate the table before a chat has been picked.
		locals[cdt][cdn].override = "Allow";
		frm.refresh_field("chat_permissions");
	},
	override(frm, cdt, cdn) {
		if (locals[cdt][cdn].telegram_chat) frm.trigger("refresh_rows");
	},
	telegram_chat(frm, cdt, cdn) {
		if (locals[cdt][cdn].telegram_chat) frm.trigger("refresh_rows");
	},
	chat_permissions_remove(frm) {
		frm.trigger("refresh_rows");
	},
});

async function mark_verified(frm) {
	if (!frm.doc.employee) {
		frappe.msgprint(__("Select the matching Employee first."));
		return;
	}
	await tam.ensure_saved(frm);
	frappe.confirm(
		__("Confirm that this Telegram account belongs to {0}?", [frappe.utils.escape_html(frm.doc.employee_name || frm.doc.employee)]),
		() => frm.call("verify_identity").then(() => frm.reload_doc())
	);
}

async function approve_dialog(frm) {
	if (!frm.doc.employee) {
		frappe.msgprint(__("Select the matching Employee first."));
		return;
	}
	await tam.ensure_saved(frm);
	const r = await frm.call("get_effective_permissions_preview");
	const rows = r.message || [];
	const allowed = rows.filter((x) => x.effective === "Allow");
	const d = new frappe.ui.Dialog({
		title: __("Approve Telegram Access"),
		size: "large",
		fields: [
			{ fieldname: "preview", fieldtype: "HTML" },
			{ fieldname: "admin_notes", fieldtype: "Small Text", label: __("Administrator Notes") },
		],
		primary_action_label: __("Approve and Send Invitations"),
		primary_action(values) {
			frm.call("approve", values).then((res) => {
				d.hide();
				const status = res.message && res.message.status;
				frappe.show_alert({
					message: status === "Approved" ? __("Approved. Invitations are being sent.") : __("Approval failed; see Error Details."),
					indicator: status === "Approved" ? "green" : "red",
				});
				frm.reload_doc();
			});
		},
	});
	tam.render_permissions(d.fields_dict.preview, rows);
	if (!allowed.length) d.get_primary_btn().prop("disabled", true);
	d.show();
}

function close_dialog(frm, method, title) {
	frappe.prompt(
		[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: method === "reject" }],
		(values) => frm.call(method, values).then(() => frm.reload_doc()),
		title,
		__("Confirm")
	);
}
