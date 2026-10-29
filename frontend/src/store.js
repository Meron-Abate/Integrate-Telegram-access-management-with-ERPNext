import { reactive } from "vue";

import { api } from "./api";

/** Session-wide info: who is signed in, what they may do, and the counters in the sidebar. */
export const boot = reactive({ loaded: false, error: null, is_manager: false, user: {}, counts: {}, bot: {} });

export async function loadBoot() {
	try {
		Object.assign(boot, await api("get_boot"), { loaded: true, error: null });
	} catch (error) {
		boot.error = error;
		boot.loaded = true;
	}
}
