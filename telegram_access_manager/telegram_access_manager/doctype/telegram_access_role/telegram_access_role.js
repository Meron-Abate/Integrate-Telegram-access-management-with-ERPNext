frappe.ui.form.on("Telegram Access Role", {
	setup(frm) {
		frm.set_query("telegram_chat", "chats", () => ({ filters: { is_active: 1 } }));
	},
	refresh(frm) {
		frm.set_intro(__("A Telegram Access Role bundles Telegram chats. It is not an ERPNext user role and grants nothing inside ERPNext."));
		const info = (frm.doc.__onload || {}).affected || {};
		const $w = frm.fields_dict.pending_changes_html.$wrapper;
		if (frm.is_new()) {
			$w.empty();
			return;
		}
		$w.html(`<p>${__("{0} active employee(s) use this role.", [info.employees || 0])}
			${info.pending ? `<b class="text-warning">${__("{0} employee(s) have changes pending.", [info.pending])}</b>` : ""}</p>`);
		if (info.pending && tam.is_manager()) {
			frm.add_custom_button(__("Apply Pending Changes to Telegram"), () =>
				frappe.confirm(__("Apply the recalculated permissions of {0} employee(s) to Telegram now?", [info.pending]), () =>
					frm.call("apply_pending_changes").then((r) => {
						frappe.show_alert({ message: __("{0} synchronization(s) queued", [r.message]), indicator: "green" });
						frm.reload_doc();
					})
				)
			).addClass("btn-primary");
		}
	},
});
