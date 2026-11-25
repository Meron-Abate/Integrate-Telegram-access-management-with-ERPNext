frappe.ui.form.on("Telegram Notification Rule", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Send Test Message"), () =>
				frm.call("send_test").then(() => frappe.show_alert({ message: __("Test message sent"), indicator: "green" }))
			);
		}
		frm.set_query("reference_doctype", () => ({ filters: { istable: 0, issingle: 0 } }));
		frm.set_query("telegram_chat", () => ({ filters: { registration_status: ["!=", "Rejected"] } }));
	},
});
