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
				People who are in a managed group or channel but were not approved here. Click one to link them to an employee and a role.
			</p>
			<ListView
				:columns="unlistedColumns"
				:rows="unlistedRows"
				row-key="name"
				:options="{ selectable: false, showTooltip: false, onRowClick: (row) => boot.is_manager && openConnect(row), emptyState: { title: 'Nobody here' } }"
			>
				<template #cell="{ column, row, item }">
					<div v-if="column.key === 'person'" class="min-w-0">
						<div class="truncate text-base font-medium text-ink-gray-8">{{ row.display_name || row.telegram_user_id }}</div>
						<div class="truncate text-sm text-ink-gray-5">{{ row.telegram_username ? "@" + row.telegram_username : row.telegram_user_id }}</div>
					</div>
					<span v-else-if="column.key === 'added_by'" class="truncate text-base text-ink-gray-7">
						{{ row.self_joined ? "Joined on their own" : row.added_by || "An admin" }}
					</span>
					<span v-else-if="column.key === 'joined_on'" class="truncate text-base text-ink-gray-7">{{ timeAgo(row.joined_on) }}</span>
					<span v-else-if="column.key === 'chat'" class="truncate text-base text-ink-gray-7">{{ row.chat }}</span>
					<div v-else-if="column.key === 'status'" class="flex items-center gap-2">
						<button
							type="button"
							class="rounded"
							:disabled="!boot.is_manager"
							@click.stop="openConnect(row)"
						>
							<Badge label="Not approved · Connect" theme="orange" variant="subtle" size="md" class="cursor-pointer" />
						</button>
						<span v-if="boot.is_manager" @click.stop>
							<Dropdown :options="rowActions(row)"><Button variant="ghost" icon="more-horizontal" /></Dropdown>
						</span>
					</div>
				</template>
			</ListView>
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

	<Dialog v-model="connectOpen" :options="{ title: 'Connect to employee', size: 'lg' }">
		<template #body-content>
			<p class="mb-3 text-base text-ink-gray-7">
				Link <b>{{ target?.display_name || target?.telegram_user_id }}</b> to an employee and a role. They get access like everyone else, and stay in the chats they are already in.
			</p>
			<div class="space-y-3">
				<div>
					<div class="mb-1 text-sm text-ink-gray-5">Employee</div>
					<EmployeePicker v-model="employee" />
				</div>
				<FormControl type="select" label="Role" :options="roleOptions" v-model="role" />
			</div>
		</template>
		<template #actions>
			<div class="flex justify-end gap-2">
				<Button label="Back" @click="connectOpen = false" />
				<Button variant="solid" label="Connect" :disabled="!employeeId" :loading="connecting" @click="connect" />
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Badge, Button, Dialog, Dropdown, FormControl, ListView, TabButtons, TextInput, debounce } from "frappe-ui";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, run, timeAgo } from "../api";
import EmployeePicker from "../components/EmployeePicker.vue";
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
const unlistedColumns = [
	{ label: "Person", key: "person", width: 2 },
	{ label: "Added by", key: "added_by", width: 1.5 },
	{ label: "When", key: "joined_on", width: 1 },
	{ label: "Group", key: "chat", width: 1.5 },
	{ label: "Status", key: "status", width: 2 },
];
const rowActions = (row) => [
	{ label: "Keep without an employee", icon: "check", onClick: () => unlistedAction(row, "keep") },
	{ label: "Remove from chat", icon: "user-x", onClick: () => unlistedAction(row, "remove") },
];
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

const connectOpen = ref(false);
const connecting = ref(false);
const target = ref(null);
const employee = ref(null);
const role = ref("");
const roles = ref([]);
const employeeId = computed(() => (typeof employee.value === "object" ? employee.value?.value : employee.value) || "");
const roleOptions = computed(() => [
	{ label: "No role (only the chats they are in)", value: "" },
	...roles.value.filter((r) => r.active).map((r) => ({ label: r.name, value: r.name })),
]);

async function openConnect(member) {
	target.value = member;
	employee.value = null;
	role.value = "";
	connectOpen.value = true;
	if (!roles.value.length) roles.value = (await api("get_roles")).roles;
}

async function connect() {
	connecting.value = true;
	const list = await run(
		api("unlisted_connect", { name: target.value.name, employee: employeeId.value, role: role.value || undefined }),
		"Connected. They now have access like the others.",
	);
	connecting.value = false;
	if (list) {
		connectOpen.value = false;
		unlisted.value = list;
		boot.counts = { ...boot.counts, unlisted: list.length };
	}
}

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
