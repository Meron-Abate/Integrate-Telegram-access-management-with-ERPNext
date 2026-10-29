import { call, toast } from "frappe-ui";

const BASE = "telegram_access_manager.api.portal.";

/** Call a portal endpoint (all access checks happen on the server). */
export function api(method, args) {
	return call(method.includes(".") ? method : BASE + method, args);
}

/** Readable message from a Frappe error (server messages are JSON strings, sometimes with HTML). */
export function errorText(error) {
	const messages = (error?.messages || [])
		.map((m) => {
			try {
				return JSON.parse(m).message || m;
			} catch {
				return m;
			}
		})
		.map((m) => String(m).replace(/<[^>]+>/g, ""));
	return messages.join(" ") || error?.message?.split("\n").pop() || "Something went wrong.";
}

/** Run an action with a success / error toast. Returns the result, or null on error. */
export async function run(promise, success) {
	try {
		const result = await promise;
		if (success) toast.success(success);
		return result;
	} catch (error) {
		toast.error(errorText(error));
		return null;
	}
}

export function timeAgo(value) {
	if (!value) return "";
	const date = new Date(String(value).replace(" ", "T"));
	const seconds = Math.round((Date.now() - date.getTime()) / 1000);
	if (seconds < 60) return "just now";
	const units = [
		["year", 31536000],
		["month", 2592000],
		["day", 86400],
		["hour", 3600],
		["minute", 60],
	];
	for (const [unit, size] of units) {
		const n = Math.floor(seconds / size);
		if (n >= 1) return `${n} ${unit}${n > 1 ? "s" : ""} ago`;
	}
	return "";
}
