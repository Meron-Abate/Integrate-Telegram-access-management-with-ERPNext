<template>
	<PageHeader title="Access Requests" />
	<div class="mx-auto max-w-5xl space-y-4 px-6 py-6">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<TabButtons :buttons="tabs" v-model="tab" />
			<TextInput v-model="search" placeholder="Search name, @username or employee" class="w-72" type="search" />
		</div>
		<ListView
			:columns="columns"
			:rows="rows"
			row-key="name"
			:options="{ selectable: false, showTooltip: false, onRowClick: (row) => router.push(`/requests/${row.name}`), emptyState: { title: emptyTitle } }"
		>
			<template #cell="{ column, row, item }">
				<div v-if="column.key === 'person'" class="min-w-0">
					<div class="truncate text-base font-medium text-ink-gray-8">{{ row.person }}</div>
					<div v-if="row.username" class="truncate text-sm text-ink-gray-5">@{{ row.username }}</div>
				</div>
				<StatusBadge v-else-if="column.key === 'status'" :label="item" />
				<span v-else-if="column.key === 'requested_on'" class="text-base text-ink-gray-6">{{ timeAgo(item) }}</span>
				<span v-else class="truncate text-base" :class="item ? 'text-ink-gray-7' : 'text-ink-gray-4'">{{ item || "—" }}</span>
			</template>
		</ListView>
	</div>
</template>

<script setup>
import { ListView, TabButtons, TextInput, debounce } from "frappe-ui";
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api, timeAgo } from "../api";
import PageHeader from "../components/PageHeader.vue";
import StatusBadge from "../components/StatusBadge.vue";

const router = useRouter();
const tabs = [
	{ label: "Waiting", value: "pending" },
	{ label: "Approved", value: "approved" },
	{ label: "Rejected / cancelled", value: "closed" },
];
const tab = ref("pending");
const search = ref("");
const rows = ref([]);
const columns = [
	{ label: "Telegram user", key: "person", width: 2 },
	{ label: "Employee", key: "employee_name", width: 2 },
	{ label: "Requested", key: "requested_on", width: 1 },
	{ label: "Status", key: "status", width: 1 },
];
const emptyTitle = computed(() => (search.value ? "No matching requests" : "No requests here"));

async function load() {
	rows.value = await api("get_requests", { tab: tab.value, search: search.value || undefined });
}
watch(tab, load, { immediate: true });
watch(search, debounce(load, 300));
</script>
