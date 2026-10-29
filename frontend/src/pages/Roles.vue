<template>
	<PageHeader title="Access Roles">
		<Button v-if="boot.is_manager" variant="solid" icon-left="plus" label="New role" @click="edit(null)" />
	</PageHeader>
	<div class="mx-auto max-w-5xl space-y-4 px-6 py-6">
		<p class="text-base text-ink-gray-5">A role is a set of groups and channels (for example a department). It's separate from ERPNext user roles.</p>
		<div class="grid gap-3 md:grid-cols-2">
			<div v-for="r in roles" :key="r.name" class="rounded-lg border p-4" :class="r.active ? '' : 'opacity-60'">
				<div class="flex items-start justify-between gap-2">
					<div>
						<div class="text-base font-semibold text-ink-gray-9">{{ r.name }}</div>
						<div class="text-sm text-ink-gray-5">{{ r.people }} {{ r.people === 1 ? "person" : "people" }} · {{ r.chats.length }} chats<span v-if="!r.active"> · inactive</span></div>
					</div>
					<Button v-if="boot.is_manager" icon="edit-2" variant="ghost" @click="edit(r)" />
				</div>
				<p v-if="r.description" class="mt-2 text-sm text-ink-gray-6">{{ r.description }}</p>
				<div class="mt-3 flex flex-wrap gap-1.5">
					<Badge v-for="c in r.chats" :key="c" :label="c" theme="gray" />
					<span v-if="!r.chats.length" class="text-sm text-ink-gray-4">No chats yet</span>
				</div>
				<div v-if="r.pending && boot.is_manager" class="mt-3 flex items-center justify-between rounded bg-surface-amber-1 px-3 py-2">
					<span class="text-sm text-ink-amber-3">{{ r.pending }} {{ r.pending === 1 ? "person has" : "people have" }} changes waiting</span>
					<Button size="sm" variant="solid" label="Apply to Telegram" @click="applyRole(r)" />
				</div>
			</div>
		</div>
		<div v-if="loaded && !roles.length" class="rounded-lg border px-4 py-10 text-center text-base text-ink-gray-5">No roles yet. Create one to group chats by team.</div>
	</div>

	<Dialog v-model="open" :options="{ title: form.name ? `Edit ${form.name}` : 'New role', size: 'lg' }">
		<template #body-content>
			<div class="space-y-4">
				<FormControl v-if="!form.name" label="Role name" v-model="form.role_name" placeholder="e.g. Production Team" />
				<FormControl type="textarea" label="Description (optional)" v-model="form.description" />
				<Switch v-model="form.active" label="Active" description="Inactive roles grant nothing." />
				<div>
					<div class="mb-2 text-sm text-ink-gray-6">Groups & channels in this role</div>
					<div class="max-h-72 divide-y overflow-y-auto rounded-lg border">
						<label v-for="c in chats" :key="c" class="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-surface-gray-1">
							<input type="checkbox" class="form-checkbox" :checked="form.chats.includes(c)" @change="toggle(c)" />
							<span class="text-base text-ink-gray-8">{{ c }}</span>
						</label>
						<div v-if="!chats.length" class="px-3 py-4 text-sm text-ink-gray-5">Activate chats under Groups & Channels first.</div>
					</div>
				</div>
				<p v-if="form.name" class="text-sm text-ink-gray-5">People with this role are recalculated. Telegram changes after you click "Apply to Telegram".</p>
			</div>
		</template>
		<template #actions>
			<div class="flex justify-end gap-2">
				<Button label="Cancel" @click="open = false" />
				<Button variant="solid" label="Save" :loading="saving" :disabled="!form.name && !form.role_name" @click="save" />
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Badge, Button, Dialog, FormControl, Switch } from "frappe-ui";
import { onMounted, reactive, ref } from "vue";

import { api, run } from "../api";
import PageHeader from "../components/PageHeader.vue";
import { boot } from "../store";

const roles = ref([]);
const chats = ref([]);
const loaded = ref(false);
const open = ref(false);
const saving = ref(false);
const form = reactive({ name: null, role_name: "", description: "", active: true, chats: [] });

async function load() {
	const data = await api("get_roles");
	roles.value = data.roles;
	chats.value = data.chats;
	loaded.value = true;
}

function edit(role) {
	Object.assign(form, {
		name: role?.name || null,
		role_name: "",
		description: role?.description || "",
		active: role ? role.active : true,
		chats: [...(role?.chats || [])],
	});
	open.value = true;
}

function toggle(chat) {
	form.chats = form.chats.includes(chat) ? form.chats.filter((c) => c !== chat) : [...form.chats, chat];
}

async function save() {
	saving.value = true;
	const data = await run(
		api("save_role", { name: form.name, role_name: form.role_name, description: form.description, active: form.active ? 1 : 0, chats: form.chats }),
		"Role saved",
	);
	saving.value = false;
	if (data) {
		open.value = false;
		await load();
	}
}

async function applyRole(role) {
	const n = await run(api("apply_role", { name: role.name }), "Changes are being applied to Telegram");
	if (n !== null) setTimeout(load, 2500);
}

onMounted(load);
</script>
