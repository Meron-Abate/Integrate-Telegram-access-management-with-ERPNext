frappe.listview_settings["Telegram Access Request"] = {
	add_fields: ["status", "verification_status"],
	get_indicator(doc) {
		const colors = {
			"Pending Verification": "orange",
			"Pending Approval": "blue",
			Approved: "green",
			Rejected: "red",
			Cancelled: "gray",
			Error: "red",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
