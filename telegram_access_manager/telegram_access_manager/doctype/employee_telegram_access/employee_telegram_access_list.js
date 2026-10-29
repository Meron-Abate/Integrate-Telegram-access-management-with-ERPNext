frappe.listview_settings["Employee Telegram Access"] = {
	add_fields: ["is_active", "sync_status", "review_required"],
	get_indicator(doc) {
		if (!doc.is_active) return [__("Revoked"), "red", "is_active,=,0"];
		if (doc.review_required) return [__("Review Required"), "red", "review_required,=,1"];
		const colors = { Synced: "green", "Changes Pending": "orange", Failed: "red", "Partially Synced": "orange" };
		return [__(doc.sync_status), colors[doc.sync_status] || "blue", "sync_status,=," + doc.sync_status];
	},
};
