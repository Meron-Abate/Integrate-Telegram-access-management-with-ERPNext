<template>
	<PageHeader title="People with Access" />
	<div class="mx-auto max-w-5xl space-y-4 px-6 py-6">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<TabButtons :buttons="tabs" v-model="tab" />
			<TextInput v-model="search" placeholder="Search employee or @username" class="w-72" type="search" />
		</div>
		<ListView
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
import { ListView, TabButtons, TextInput, debounce } from "frappe-ui";
import { ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "../api";
import PageHeader from "../components/PageHeader.vue";
import StatusBadge from "../components/StatusBadge.vue";

const router = useRouter();
const route = useRoute();
const tabs = [
	{ label: "Active", value: "active" },
	{ label: "Needs attention", value: "attention" },
	{ label: "Revoked", value: "revoked" },
];
const tab = ref(route.query.tab || "active");
const search = ref("");
const rows = ref([]);
const columns = [
	{ label: "Employee", key: "employee_name", width: 2 },
	{ label: "Role", key: "role", width: 1.5 },
	{ label: "Chats", key: "chats", width: 0.7 },
	{ label: "Status", key: "status", width: 1.2 },
];

async function load() {
	rows.value = await api("get_people", { tab: tab.value, search: search.value || undefined });
}
watch(tab, load, { immediate: true });
watch(search, debounce(load, 300));
</script>
