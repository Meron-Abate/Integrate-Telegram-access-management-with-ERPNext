<template>
	<PageHeader :title="req?.person || 'Request'" back="/requests">
		<template #badge><StatusBadge v-if="req" :label="req.status" /></template>
		<template v-if="req">
			<Button v-if="dirty" label="Save" :loading="saving" @click="save" />
			<Button
				v-if="req.editable && !boot.is_manager && req.status === 'Pending Verification'"
				variant="solid"
				label="Mark Verified"
				:disabled="!employee"
				@click="verify"
			/>
			<Button v-if="req.editable && boot.is_manager" variant="solid" label="Approve" :disabled="!employee" @click="openApprove" />
			<Dropdown v-if="req.editable && boot.is_manager" :options="moreActions">
				<Button icon="more-horizontal" />
			</Dropdown>
			<Button v-if="canResend" label="Resend message" @click="resend" />
		</template>
	</PageHeader>

	<div v-if="!req" class="flex justify-center py-16"><LoadingIndicator class="h-6 w-6 text-ink-gray-5" /></div>
	<div v-else class="mx-auto grid max-w-5xl gap-6 px-6 py-6 md:grid-cols-3">
		<div class="space-y-6 md:col-span-2">
			<div v-if="req.error" class="rounded-lg border border-outline-red-1 bg-surface-red-1 px-4 py-3 text-base text-ink-red-4">
				{{ req.error }}
			</div>

			<section>
				<h2 class="mb-1 text-base font-semibold text-ink-gray-8">1. Employee name</h2>
				<p class="mb-3 text-sm text-ink-gray-5">Choose the employee this Telegram account belongs to. The name on Telegram alone is not proof.</p>
				<EmployeePicker v-if="req.editable" v-model="employee" />
				<div v-else class="text-base text-ink-gray-8">{{ req.employee_name || "—" }}</div>
			</section>

			<section>
				<h2 class="mb-1 text-base font-semibold text-ink-gray-8">2. What can they access?</h2>
				<p class="mb-3 text-sm text-ink-gray-5">Pick a role to switch on its groups and channels, then switch individual chats on or off.</p>
				<div class="mb-3 w-72">
					<FormControl v-if="req.editable" type="select" :options="roleOptions" v-model="role" />
					<div v-else class="text-base text-ink-gray-8">Role: {{ req.role || "None" }}</div>
				</div>
				<ChatAccess :chats="chatRows" v-model:value="access" :editable="req.editable" />
				<p class="mt-2 text-sm text-ink-gray-5">{{ grantedCount }} of {{ chatRows.length }} chats switched on.</p>
			</section>
		</div>

		<aside class="space-y-4">
			<div class="rounded-lg border p-4">
				<h3 class="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-gray-5">Telegram account</h3>
				<dl class="space-y-2 text-base">
					<div><dt class="text-sm text-ink-gray-5">Name</dt><dd class="text-ink-gray-8">{{ req.person }}</dd></div>
					<div><dt class="text-sm text-ink-gray-5">Username</dt><dd class="text-ink-gray-8">{{ req.username ? "@" + req.username : "None" }}</dd></div>
					<div><dt class="text-sm text-ink-gray-5">Telegram ID</dt><dd class="font-mono text-ink-gray-8">{{ req.telegram_user_id }}</dd></div>
					<div><dt class="text-sm text-ink-gray-5">Requested</dt><dd class="text-ink-gray-8">{{ timeAgo(req.requested_on) }}</dd></div>
				</dl>
			</div>
			<div v-if="!req.editable" class="rounded-lg border p-4">
				<h3 class="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-gray-5">Decision</h3>
				<dl class="space-y-2 text-base">
					<div><dt class="text-sm text-ink-gray-5">By</dt><dd class="text-ink-gray-8">{{ req.decided_by || "—" }}</dd></div>
					<div><dt class="text-sm text-ink-gray-5">When</dt><dd class="text-ink-gray-8">{{ timeAgo(req.decided_on) }}</dd></div>
					<div v-if="req.notes"><dt class="text-sm text-ink-gray-5">Notes</dt><dd class="whitespace-pre-line text-ink-gray-8">{{ req.notes }}</dd></div>
					<div><dt class="text-sm text-ink-gray-5">Message to employee</dt><dd class="text-ink-gray-8">{{ req.notification }}</dd></div>
				</dl>
				<RouterLink v-if="req.access_record" :to="`/people/${req.access_record}`" class="mt-3 inline-block text-base text-ink-blue-3 hover:underline">
					Open their access →
				</RouterLink>
			</div>
		</aside>
	</div>

	<Dialog v-model="approveOpen" :options="{ title: 'Approve access', size: 'lg' }">
		<template #body-content>
			<p class="mb-3 text-base text-ink-gray-7">
				<b>{{ employee?.label || req?.employee_name }}</b> will get invitation links for:
			</p>
			<ul class="mb-4 list-disc space-y-1 pl-5 text-base text-ink-gray-8">
				<li v-for="c in grantedChats" :key="c">{{ c }}</li>
			</ul>
			<FormControl type="textarea" label="Note (optional)" v-model="notes" />
		</template>
		<template #actions>
			<div class="flex justify-end gap-2">
				<Button label="Back" @click="approveOpen = false" />
				<Button variant="solid" label="Approve and send invitations" :loading="busy" :disabled="!grantedChats.length" @click="approve" />
			</div>
		</template>
	</Dialog>

	<Dialog v-model="closeOpen" :options="{ title: closeAction === 'reject' ? 'Reject request' : 'Cancel request', size: 'md' }">
		<template #body-content>
			<FormControl type="textarea" label="Reason" v-model="reason" :placeholder="closeAction === 'reject' ? 'Shown in the audit log' : ''" />
		</template>
		<template #actions>
			<div class="flex justify-end gap-2">
				<Button label="Back" @click="closeOpen = false" />
				<Button variant="solid" theme="red" :label="closeAction === 'reject' ? 'Reject' : 'Cancel request'" :loading="busy" :disabled="closeAction === 'reject' && !reason" @click="close" />
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog, Dropdown, FormControl, LoadingIndicator } from "frappe-ui";
import { computed, onMounted, ref, watch } from "vue";

import { api, run, timeAgo } from "../api";
import ChatAccess from "../components/ChatAccess.vue";
import EmployeePicker from "../components/EmployeePicker.vue";
import PageHeader from "../components/PageHeader.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { boot, loadBoot } from "../store";

const props = defineProps({ name: String });

const req = ref(null);
const roles = ref([]);
const employee = ref(null);
const role = ref("");
const access = ref({});
const snapshot = ref("");
const saving = ref(false);
const busy = ref(false);
const approveOpen = ref(false);
const closeOpen = ref(false);
const closeAction = ref("reject");
const notes = ref("");
const reason = ref("");

const roleChats = computed(() => new Set(roles.value.find((r) => r.name === role.value)?.chats || []));
const roleOptions = computed(() => [
	{ label: "No role (choose chats individually)", value: "" },
	...roles.value.filter((r) => r.active).map((r) => ({ label: r.name, value: r.name })),
]);
const chatRows = computed(() => (req.value?.chats || []).map((c) => ({ ...c, in_role: roleChats.value.has(c.chat) })));
const grantedChats = computed(() => chatRows.value.filter((c) => access.value[c.chat]).map((c) => c.chat));
const grantedCount = computed(() => grantedChats.value.length);
const employeeId = computed(() => (typeof employee.value === "object" ? employee.value?.value : employee.value) || "");
const state = computed(() => JSON.stringify([employeeId.value, role.value, access.value]));
const dirty = computed(() => req.value?.editable && state.value !== snapshot.value);
const canResend = computed(() => boot.is_manager && req.value && !req.value.editable && req.value.notification === "Failed");
const moreActions = [
	{ label: "Reject", icon: "x-circle", onClick: () => openClose("reject") },
	{ label: "Cancel request", icon: "slash", onClick: () => openClose("cancel") },
];

let loading = false;

function apply(data) {
	loading = true;
	req.value = data;
	employee.value = data.employee ? { label: data.employee_name, value: data.employee } : null;
	role.value = data.role || "";
	access.value = Object.fromEntries(data.chats.map((c) => [c.chat, c.access]));
	snapshot.value = state.value;
	loading = false;
}

// Changing the role switches its chats on (and the previous role's chats off), keeping individual choices.
watch(
	role,
	(next, prev) => {
		if (loading || !req.value) return; // values coming from the server are already correct
		const before = new Set(roles.value.find((r) => r.name === prev)?.chats || []);
	const after = roleChats.value;
		const updated = { ...access.value };
		for (const chat of before) if (!after.has(chat)) updated[chat] = false;
		for (const chat of after) updated[chat] = true;
		access.value = updated;
	},
	{ flush: "sync" },
);

async function save() {
	saving.value = true;
	const data = await run(api("save_request", { name: props.name, employee: employeeId.value, role: role.value, access: access.value }), "Saved");
	saving.value = false;
	if (data) apply(data);
	return data;
}

async function ensureSaved() {
	return dirty.value ? !!(await save()) : true;
}

async function openApprove() {
	if (await ensureSaved()) approveOpen.value = true;
}

async function approve() {
	busy.value = true;
	const data = await run(api("approve_request", { name: props.name, notes: notes.value }), "Approved. Invitations are being sent.");
	busy.value = false;
	if (data) {
		approveOpen.value = false;
		apply(data);
		loadBoot();
	}
}

async function verify() {
	if (!(await ensureSaved())) return;
	const data = await run(api("verify_request", { name: props.name }), "Marked as verified. A manager can now approve.");
	if (data) apply(data);
}

async function openClose(action) {
	closeAction.value = action;
	reason.value = "";
	closeOpen.value = true;
}

async function close() {
	busy.value = true;
	const data = await run(api("close_request", { name: props.name, action: closeAction.value, reason: reason.value }), closeAction.value === "reject" ? "Request rejected" : "Request cancelled");
	busy.value = false;
	if (data) {
		closeOpen.value = false;
		apply(data);
		loadBoot();
	}
}

async function resend() {
	await run(api("resend_request_notification", { name: props.name }), "Message queued");
}

onMounted(async () => {
	const [data, roleData] = await Promise.all([api("get_request", { name: props.name }), api("get_roles")]);
	roles.value = roleData.roles;
	apply(data);
});
</script>
