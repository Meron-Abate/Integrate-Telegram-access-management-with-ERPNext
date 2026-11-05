<template>
	<PageHeader title="People with Access" />
	<div class="mx-auto max-w-5xl space-y-4 px-6 py-6">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<TabButtons :buttons="tabs" v-model="tab" />
			<TextInput v-if="tab !== 'unlisted'" v-model="search" placeholder="Search employee or @username" class="w-72" type="search" />
			<FormControl v-else type="select" :options="howOptions" v-model="how" class="w-56" />
		</div>

		<div v-if="tab === 'unlisted'" class="space-y-3">
			<p class="text-sm text-ink-gray-5">
				People who are in a managed group or channel but were not approved here. Remove them from the chat, or keep them.
			</p>
			<div class="divide-y rounded-lg border">
				<div v-for="m in unlistedRows" :key="m.name" class="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
					<div class="min-w-0">
						<div class="truncate text-base font-medium text-ink-gray-8">
							{{ m.display_name || m.telegram_user_id }}<span v-if="m.telegram_username" class="font-normal text-ink-gray-5"> @{{ m.telegram_username }}</span>
						</div>
						<div class="text-sm text-ink-gray-5">
							{{ m.chat }} · {{ m.self_joined ? "joined on their own" : m.added_by ? "added by " + m.added_by : "added by an admin" }} · {{ timeAgo(m.joined_on) }}
						</div>
					</div>
					<div v-if="boot.is_manager" class="flex gap-2">
						<Button label="Keep" @click="unlistedAction(m, 'keep')" />
						<Button theme="red" label="Remove from chat" @click="unlistedAction(m, 'remove')" />
					</div>
				</div>
				<div v-if="!unlistedRows.length" class="px-4 py-6 text-center text-base text-ink-gray-5">Nobody here.</div>
			</div>
		</div>

		<ListView
			v-else
			:columns="columns"
			:rows="rows"
			row-key="name"
			:options="{ selectable: false, showTooltip: false, onRowClick: (row) => router.push(`/people/${row.name}`), emptyState: { title: 'Nobody here' } }"
		>
			<template #cell="{ column, row, item }">
				<div v-if="column.key === 'employee_name'" class="min-w-0">
					<div class="truncate text-base font-medium text-ink-gray-8">{{ row.employee_name }}</div>
					<div class="truncate text-sm text-ink-gray-5">{{ row.username ? "@" + row.username : row.employee }}</div>
				</div>
				<StatusBadge v-else-if="column.key === 'status'" :label="item" />
				<span v-else class="truncate text-base" :class="item ? 'text-ink-gray-7' : 'text-ink-gray-4'">{{ item || "—" }}</span>
			</template>
		</ListView>
	</div>
</template>

<script setup>
import { Button, FormControl, ListView, TabButtons, TextInput, debounce } from "frappe-ui";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, run, timeAgo } from "../api";
import PageHeader from "../components/PageHeader.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { boot } from "../store";

const router = useRouter();
const route = useRoute();
const tabs = computed(() => [
	{ label: "Active", value: "active" },
	{ label: "Needs attention", value: "attention" },
	{ label: boot.counts?.unlisted ? `Not approved (${boot.counts.unlisted})` : "Not approved", value: "unlisted" },
	{ label: "Revoked", value: "revoked" },
]);
const tab = ref(route.query.tab || "active");
const search = ref("");
const rows = ref([]);
const unlisted = ref([]);
const how = ref("all");
const howOptions = [
	{ label: "All", value: "all" },
	{ label: "Added by an admin", value: "admin" },
	{ label: "Joined on their own", value: "self" },
];
const unlistedRows = computed(() => unlisted.value.filter((m) => how.value === "all" || (how.value === "self") === !!m.self_joined));
const columns = [
	{ label: "Employee", key: "employee_name", width: 2 },
	{ label: "Role", key: "role", width: 1.5 },
	{ label: "Chats", key: "chats", width: 0.7 },
	{ label: "Status", key: "status", width: 1.2 },
];

async function unlistedAction(member, action) {
	const list = await run(api("unlisted_action", { name: member.name, action }), action === "remove" ? "Removed from the chat" : "Kept");
	if (list) {
		unlisted.value = list;
		boot.counts = { ...boot.counts, unlisted: list.length };
	}
}

async function load() {
	if (tab.value === "unlisted") {
		unlisted.value = await api("get_unlisted");
		return;
	}
	rows.value = await api("get_people", { tab: tab.value, search: search.value || undefined });
}
watch(tab, load, { immediate: true });
watch(search, debounce(load, 300));
</script>
