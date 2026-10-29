// The Telegram Access workspace lives in the portal; send anyone who opens the desk page there.
(() => {
	const go = () => {
		if (window.location.pathname.replace(/\/$/, "") === "/desk/telegram-access-management") {
			window.location.replace("/telegram-access/");
		}
	};
	go();
	$(document).on("startup page-change", go);
	frappe.router?.on?.("change", go);
})();
