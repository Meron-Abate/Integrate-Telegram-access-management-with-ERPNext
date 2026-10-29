<template>
	<PageHeader :title="person?.employee_name || 'Employee'" back="/people">
		<template #badge><StatusBadge v-if="person" :label="person.status" /></template>
		<template v-if="person && boot.is_manager">
			<Button v-if="dirty" variant="solid" label="Save & apply to Telegram" :loading="saving" @click="save" />
			<Button v-if="!person.active" variant="solid" label="Restore access" @click="action('restore', 'Access restored. Switch chats on and apply.')" />
			<Dropdown :options="moreActions"><Button icon="more-horizontal" /></Dropdown>
		</template>
	</PageHeader>

	<div v-if="!person" class="flex justify-center py-16"><LoadingIndicator class="h-6 w-6 text-ink-gray-5" /></div>
	<div v-else class="mx-auto grid max-w-5xl gap-6 px-6 py-6 md:grid-cols-3">
		<div class="space-y-6 md:col-span-2">
			<div v-if="person.review_reason" class="flex items-center justify-between gap-3 rounded-lg border border-outline-amber-1 bg-surface-amber-1 px-4 py-3">
				<div class="text-base text-ink-amber-3"><b>Review needed:</b> {{ person.review_reason }}</div>
				<Button v-if="boot.is_manager" theme="red" label="Revoke access" @click="revokeOpen = true" />
			</div>
			<div v-if="!person.active" class="rounded-lg border bg-surface-gray-1 px-4 py-3 text-base text-ink-gray-6">
				Access is revoked. The employee has been removed from managed chats where Telegram allowed it.
			</div>

			<section>
				<h2 class="mb-1 text-base font-semibold text-ink-gray-8">Groups & channels</h2>
				<p class="mb-3 text-sm text-ink-gray-5">Switch chats on or off, then save. Telegram is only changed when you save.</p>
				<div class="mb-3 w-72">
					<FormControl v-if="editable" type="select" :options="roleOptions" v-model="role" />
					<div v-else class="text-base text-ink-gray-8">Role: {{ person.role || "None" }}</div>
				</div>
				<ChatAccess :chats="chatRows" v-model:value="access" :editable="editable" show-membership />
			</section>
		</div>

		<aside class="space-y-4">
			<div class="rounded-lg border p-4">
				<h3 class="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-gray-5">Telegram account</h3>
				<dl class="space-y-2 text-base">
					<div><dt class="text-sm text-ink-gray-5">Name</dt><dd class="text-ink-gray-8">{{ person.display_name || "—" }}</dd></div>
					<div><dt class="text-sm text-ink-gray-5">Username</dt><dd class="text-ink-gray-8">{{ person.username ? "@" + person.username : "None" }}</dd></div>
					<div><dt class="text-sm text-ink-gray-5">Telegram ID</dt><dd class="font-mono text-ink-gray-8">{{ person.telegram_user_id }}</dd></div>
				</dl>
			</div>
			<div class="rounded-lg border p-4">
				<h3 class="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-gray-5">History</h3>
				<dl class="space-y-2 text-base">
					<div><dt class="text-sm text-ink-gray-5">Approved by</dt><dd class="text-ink-gray-8">{{ person.approved_by || "—" }}</dd></div>
					<div><dt class="text-sm text-ink-gray-5">Approved</dt><dd class="text-ink-gray-8">{{ timeAgo(person.approved_on) || "—" }}</dd></div>
					<div><dt class="text-sm text-ink-gray-5">Last checked with Telegram</dt><dd class="text-ink-gray-8">{{ timeAgo(person.last_sync_on) || "Not yet" }}</dd></div>
				</dl>
			</div>
		</aside>
	</div>

	<Dialog v-model="revokeOpen" :options="{ title: 'Revoke all Telegram access', size: 'md' }">
		<template #body-content>
			<p class="mb-3 text-base text-ink-gray-7">The bot removes {{ person?.employee_name }} from every managed group and channel and cancels their invitation links.</p>
			<FormControl type="textarea" label="Reason" v-model="reason" />
			<div class="mt-3"><FormControl type="checkbox" label="Send them a message on Telegram" v-model="notify" /></div>
		</template>
		<template #actions>
			<div class="flex justify-end gap-2">
				<Button label="Back" @click="revokeOpen = false" />
				<Button variant="solid" theme="red" label="Revoke access" :disabled="!reason" :loading="busy" @click="revoke" />
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog, Dropdown, FormControl, LoadingIndicator } from "frappe-ui";
import { computed, onMounted, ref, watch } from "vue";

import { api, run, timeAgo } from "../api";
import ChatAccess from "../components/ChatAccess.vue";
import PageHeader from "../components/PageHeader.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { boot, loadBoot } from "../store";

const props = defineProps({ name: String });

const person = ref(null);
const roles = ref([]);
const role = ref("");
const access = ref({});
const snapshot = ref("");
const saving = ref(false);
const busy = ref(false);
const revokeOpen = ref(false);
const reason = ref("");
const notify = ref(true);
let loading = false;

const editable = computed(() => boot.is_manager && person.value?.active);
const roleChats = computed(() => new Set(roles.value.find((r) => r.name === role.value)?.chats || []));
const roleOptions = computed(() => [
	{ label: "No role (choose chats individually)", value: "" },
	...roles.value.filter((r) => r.active).map((r) => ({ label: r.name, value: r.name })),
]);
const chatRows = computed(() => (person.value?.chats || []).map((c) => ({ ...c, in_role: roleChats.value.has(c.chat) })));
const state = computed(() => JSON.stringify([role.value, access.value]));
const dirty = computed(() => editable.value && state.value !== snapshot.value);
const hasProblem = computed(() => (person.value?.chats || []).some((c) => c.problem));
const moreActions = computed(() => {
	if (!person.value?.active) return [];
	const items = [{ label: "Check status in Telegram", icon: "refresh-cw", onClick: () => action("refresh", "Checking with Telegram…") }];
	if (hasProblem.value) items.push({ label: "Retry problems", icon: "rotate-ccw", onClick: () => action("retry", "Retrying…") });
	items.push({ label: "Revoke access", icon: "user-x", onClick: () => (revokeOpen.value = true) });
	return items;
});

function apply(data) {
	loading = true;
	person.value = data;
	role.value = data.role || "";
	access.value = Object.fromEntries(data.chats.map((c) => [c.chat, c.access]));
	snapshot.value = state.value;
	loading = false;
}

watch(
	role,
	(next, prev) => {
		if (loading || !person.value) return;
		const before = new Set(roles.value.find((r) => r.name === prev)?.chats || []);
		const updated = { ...access.value };
		for (const chat of before) if (!roleChats.value.has(chat)) updated[chat] = false;
		for (const chat of roleChats.value) updated[chat] = true;
		access.value = updated;
	},
	{ flush: "sync" },
);

async function save() {
	saving.value = true;
	const data = await run(api("save_person", { name: props.name, role: role.value, access: access.value }), "Saved. Telegram is being updated.");
	saving.value = false;
	if (data) {
		apply(data);
		setTimeout(reload, 2500);
	}
}

async function action(name, message) {
	const data = await run(api("person_action", { name: props.name, action: name }), message);
	if (data) {
		apply(data);
		loadBoot();
		setTimeout(reload, 2500);
	}
}

async function revoke() {
	busy.value = true;
	const data = await run(api("person_action", { name: props.name, action: "revoke", reason: reason.value, notify: notify.value ? 1 : 0 }), "Access revoked");
	busy.value = false;
	if (data) {
		revokeOpen.value = false;
		apply(data);
		loadBoot();
	}
}

async function reload() {
	if (!dirty.value) apply(await api("get_person", { name: props.name }));
}

onMounted(async () => {
	const [data, roleData] = await Promise.all([api("get_person", { name: props.name }), api("get_roles")]);
	roles.value = roleData.roles;
	apply(data);
});
</script>
