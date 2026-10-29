frappe.ui.form.on("Telegram Chat", {
	refresh(frm) {
		const colors = { Administrator: "green", Member: "orange", "Not a Member": "red", Error: "red", Unknown: "gray" };
		frm.page.set_indicator(__("Bot: {0}", [__(frm.doc.bot_status || "Unknown")]), colors[frm.doc.bot_status] || "gray");
		const review = ["Discovered", "Pending Review"].includes(frm.doc.registration_status) && !frm.doc.is_active;
		if (review) {
			frm.set_intro(
				__("Registered from Telegram and awaiting review. Check the Community, validate bot access, then tick Active and save — or reject the registration."),
				"orange"
			);
			if (tam.is_manager()) {
				frm.add_custom_button(__("Reject Registration"), () =>
					frappe.prompt(
						[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason") }],
						(values) => frm.call("reject_registration", values).then(() => frm.reload_doc()),
						__("Reject Registration"),
						__("Reject")
					)
				);
			}
		} else if (frm.doc.registration_status === "Rejected") {
			frm.set_intro(__("Registration rejected. /register is refused for this chat. Tick Active and save if you decide to manage it after all."), "red");
		}
		if (frm.is_new()) {
			frm.set_intro(
				__(
					"Add the bot as an administrator of the group/channel first (rights: 'Invite users via link', and 'Ban users' for automatic removal). " +
						"The numeric chat ID can be obtained from the Telegram Bot Update list after adding the bot, or from a trusted ID bot; " +
						"supergroup/channel IDs start with -100."
				),
				"blue"
			);
		}
		if (!frm.is_new() && tam.is_manager()) {
			frm.add_custom_button(__("Validate Bot Access"), async () => {
				await tam.ensure_saved(frm);
				const r = await frm.call("validate_bot_access");
				const res = r.message || {};
				const msgs = (res.messages || []).map((m) => `<li>${frappe.utils.escape_html(m)}</li>`).join("");
				frappe.msgprint({
					title: res.ok ? __("Bot access OK") : __("Bot access problem"),
					indicator: res.ok ? "green" : "red",
					message: res.error ? frappe.utils.escape_html(res.error) : msgs ? `<ul>${msgs}</ul>` : __("The bot can manage invitations for this chat."),
				});
				frm.reload_doc();
			}).addClass("btn-primary");
		}
	},
});
