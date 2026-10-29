frappe.listview_settings["Telegram Chat"] = {
	add_fields: ["is_active", "bot_status", "registration_status"],
	get_indicator(doc) {
		if (doc.registration_status === "Pending Review" && !doc.is_active) return [__("Pending Review"), "orange", "registration_status,=,Pending Review"];
		if (doc.registration_status === "Discovered" && !doc.is_active) return [__("Discovered"), "blue", "registration_status,=,Discovered"];
		if (doc.registration_status === "Rejected" && !doc.is_active) return [__("Rejected"), "red", "registration_status,=,Rejected"];
		if (!doc.is_active) return [__("Inactive"), "gray", "is_active,=,0"];
		if (doc.bot_status !== "Administrator") return [__("Bot not admin"), "red", "bot_status,!=,Administrator"];
		return [__("Active"), "green", "is_active,=,1"];
	},
};
