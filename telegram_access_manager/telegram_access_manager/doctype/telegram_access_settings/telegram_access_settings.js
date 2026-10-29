frappe.ui.form.on("Telegram Access Settings", {
	setup(frm) {
		frm.set_query("default_telegram_community", () => ({ filters: { is_active: 1 } }));
	},

	refresh(frm) {
		frm.trigger("render_status");
		if (!tam.is_manager()) return;
		const call = (method, args) =>
			frappe.call({ method: `telegram_access_manager.api.admin.${method}`, args, freeze: true }).then((r) => {
				frm.reload_doc();
				return r.message;
			});
		frm.add_custom_button(__("Test Connection"), () =>
			call("test_connection").then((m) => frappe.msgprint(__("Connected as @{0} (ID {1})", [m.username, m.id])))
		, __("Telegram"));
		frm.add_custom_button(__("Set Webhook"), () =>
			frappe.confirm(__("Register {0} as the bot's webhook?", [((frm.doc.__onload || {}).config_status || {}).webhook_url || ""]), () =>
				call("set_webhook").then(() => frappe.show_alert({ message: __("Webhook registered"), indicator: "green" }))
			)
		, __("Telegram"));
		frm.add_custom_button(__("Generate Webhook Secret"), () =>
			frappe.confirm(__("Generate a new random webhook secret? In Webhook mode, click Set Webhook afterwards."), () =>
				frm.call("generate_webhook_secret").then(() => {
					frappe.show_alert({ message: __("New webhook secret saved"), indicator: "green" });
					frm.reload_doc();
				})
			)
		, __("Telegram"));
		frm.add_custom_button(__("Webhook Info"), () =>
			call("get_webhook_info").then((m) =>
				frappe.msgprint(`<pre>${frappe.utils.escape_html(JSON.stringify(m, null, 2))}</pre>`, __("Webhook Info"))
			)
		, __("Telegram"));
		frm.add_custom_button(__("Delete Webhook"), () =>
			frappe.confirm(__("Delete the webhook? Telegram will stop delivering updates until it is set again or polling is started."), () =>
				call("delete_webhook").then(() => frappe.show_alert({ message: __("Webhook deleted"), indicator: "orange" }))
			)
		, __("Telegram"));
	},

	render_status(frm) {
		const s = (frm.doc.__onload || {}).config_status || {};
		const row = (label, ok, text) =>
			`<tr><td>${label}</td><td><span class="indicator-pill ${ok ? "green" : "red"}">${frappe.utils.escape_html(text)}</span></td></tr>`;
		frm.fields_dict.status_html.$wrapper.html(`
			<table class="table table-sm table-bordered" style="max-width: 640px">
				${row(__("Bot token"), s.token_valid, s.token_valid ? __("Configured via {0}", [s.token_source]) : s.token_configured ? __("Invalid format") : __("Missing"))}
				${row(__("Webhook secret"), s.secret_valid, s.secret_valid ? __("Configured via {0}", [s.secret_source]) : s.secret_configured ? __("Invalid format") : __("Missing"))}
				${row(__("Webhook URL"), !!s.webhook_url, s.webhook_url || __("Set Public Base URL"))}
			</table>
			<p class="text-muted small">${__(
				"Secrets are used only on the server. Order of precedence: TELEGRAM_BOT_TOKEN / TELEGRAM_WEBHOOK_SECRET environment variables, then the encrypted Bot Credentials above, then site_config.json. They are never sent to the browser."
			)}</p>`);
	},
});
