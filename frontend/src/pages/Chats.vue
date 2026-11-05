<template>
	<PageHeader title="Groups & Channels">
		<Button icon-left="help-circle" label="How to add a chat" @click="helpOpen = true" />
	</PageHeader>
	<div class="mx-auto max-w-5xl space-y-4 px-6 py-6">
		<TabButtons :buttons="tabs" v-model="tab" />
		<div class="divide-y rounded-lg border">
			<div v-for="chat in rows" :key="chat.name" class="flex flex-wrap items-center gap-3 px-4 py-3">
				<FeatherIcon :name="chat.type === 'Channel' ? 'radio' : 'users'" class="h-4 w-4 shrink-0 text-ink-gray-5" />
				<div class="min-w-0 flex-1">
					<div class="truncate text-base font-medium text-ink-gray-8">{{ chat.name }}</div>
					<div class="text-sm text-ink-gray-5">
						{{ chat.type }}<span v-if="chat.community"> · {{ chat.community }}</span>
						<span v-if="chat.registered_by && tab === 'review'"> · registered by {{ chat.registered_by }} {{ timeAgo(chat.registered_on) }}</span>
					</div>
				</div>
				<StatusBadge :label="chat.bot" />
				<Button v-if="chat.status === 'Active'" label="Members" @click="openMembers(chat)" />
				<template v-if="boot.is_manager">
					<Button label="Check bot" :loading="busy === chat.name + 'check'" @click="act(chat, 'check')" />
					<Button v-if="chat.status !== 'Active'" variant="solid" label="Activate" :loading="busy === chat.name + 'activate'" @click="act(chat, 'activate')" />
					<Button v-if="chat.status === 'To review'" theme="red" label="Reject" @click="openReject(chat)" />
					<Button v-if="chat.status === 'Active'" label="Deactivate" :loading="busy === chat.name + 'deactivate'" @click="act(chat, 'deactivate')" />
				</template>
			</div>
			<div v-if="loaded && !rows.length" class="px-4 py-10 text-center text-base text-ink-gray-5">
				{{ tab === "review" ? "Nothing to review. New chats appear here after /register." : "No chats here." }}
			</div>
		</div>
	</div>

	<Dialog v-model="membersOpen" :options="{ title: `Members of ${membersChat?.name || ''}`, size: '2xl' }">
		<template #body-content>
			<div v-if="!members" class="flex justify-center py-8"><LoadingIndicator class="h-6 w-6 text-ink-gray-5" /></div>
			<template v-else>
				<p class="mb-1 text-sm text-ink-gray-5">
					<span v-if="members.telegram_count != null">Telegram says <b>{{ members.telegram_count }}</b> people are in this chat. </span>
					<b>{{ members.known_joined }}</b> of them are members approved here<span v-if="members.not_approved">, and <b>{{ members.not_approved }}</b> are not approved yet</span>.
				</p>
				<p class="mb-3 text-sm text-ink-gray-5">
					Telegram does not let the bot list everyone in a group, so this shows the people ERPNext knows about. To give someone access for just this group, open them under People and switch the group on or off.
				</p>
				<div class="max-h-96 space-y-4 overflow-y-auto">
					<div v-for="section in sections" :key="section.title">
						<h3 class="mb-1 text-sm font-semibold text-ink-gray-7">{{ section.title }}</h3>
						<div class="divide-y rounded-lg border">
							<RouterLink
								v-for="m in section.rows"
								:key="m.kind + m.name"
								:to="m.kind === 'approved' ? `/people/${m.name}` : '/people?tab=unlisted'"
								class="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-surface-gray-1"
							>
								<div class="min-w-0">
									<div class="truncate text-base font-medium text-ink-gray-8">
										{{ m.person }}<span v-if="m.username" class="font-normal text-ink-gray-5"> @{{ m.username }}</span>
										<span v-if="m.admin" class="ml-1 rounded bg-surface-gray-2 px-1.5 py-0.5 text-xs font-normal text-ink-gray-6">Admin</span>
									</div>
									<div class="text-sm text-ink-gray-5">
										<template v-if="m.kind === 'approved'">{{ m.role || "No role" }}<span v-if="!m.allowed"> · access being removed</span></template>
										<template v-else>{{ m.self_joined ? "joined on their own" : m.added_by ? "added by " + m.added_by : "added by an admin" }} · connect them to an employee under People → Not approved</template>
									</div>
								</div>
								<StatusBadge :label="m.status" />
							</RouterLink>
						</div>
					</div>
					<div v-if="!sections.length" class="rounded-lg border px-4 py-8 text-center text-base text-ink-gray-5">Nobody known yet.</div>
				</div>
			</template>
		</template>
	</Dialog>

	<Dialog v-model="rejectOpen" :options="{ title: 'Reject this chat', size: 'md' }">
		<template #body-content>
			<p class="mb-3 text-base text-ink-gray-7">{{ rejecting?.name }} won't be managed, and /register will be refused for it.</p>
			<FormControl type="textarea" label="Reason (optional)" v-model="reason" />
		</template>
		<template #actions>
			<div class="flex justify-end gap-2">
				<Button label="Back" @click="rejectOpen = false" />
				<Button variant="solid" theme="red" label="Reject" @click="reject" />
			</div>
		</template>
	</Dialog>

	<Dialog v-model="helpOpen" :options="{ title: 'Adding a group or channel', size: 'lg' }">
		<template #body-content>
			<ol class="list-decimal space-y-2 pl-5 text-base text-ink-gray-7">
				<li>In Telegram, make <b>@{{ boot.bot.username || "your bot" }}</b> an administrator with <i>Invite users via link</i> and <i>Ban users</i>.</li>
				<li><b>Group:</b> send <code class="rounded bg-surface-gray-2 px-1">/register</code> in the group.</li>
				<li><b>Channel:</b> send <code class="rounded bg-surface-gray-2 px-1">/register</code> to the bot privately, then post the code it gives you in the channel.</li>
				<li>Come back here, open <b>To review</b>, click <b>Check bot</b> and then <b>Activate</b>.</li>
			</ol>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog, FeatherIcon, FormControl, LoadingIndicator, TabButtons, toast } from "frappe-ui";
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { api, run, timeAgo } from "../api";
import PageHeader from "../components/PageHeader.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { boot, loadBoot } from "../store";

const route = useRoute();
const tabs = [
	{ label: "To review", value: "review" },
	{ label: "Active", value: "active" },
	{ label: "Inactive", value: "inactive" },
];
const tab = ref(route.query.tab || (boot.counts.chats_to_review ? "review" : "active"));
const rows = ref([]);
const loaded = ref(false);
const busy = ref("");
const helpOpen = ref(false);
const rejectOpen = ref(false);
const rejecting = ref(null);
const reason = ref("");
const membersOpen = ref(false);
const membersChat = ref(null);
const members = ref(null);
const sections = computed(() => {
	const all = members.value?.members || [];
	return [
		{ title: "Members", rows: all.filter((m) => m.kind === "approved") },
		{ title: "Not approved yet (not counted as members)", rows: all.filter((m) => m.kind === "not_approved") },
	].filter((section) => section.rows.length);
});

async function openMembers(chat) {
	membersChat.value = chat;
	members.value = null;
	membersOpen.value = true;
	members.value = await api("get_chat_members", { name: chat.name });
}

async function load() {
	loaded.value = false;
	rows.value = await api("get_chats", { tab: tab.value });
	loaded.value = true;
}

const MESSAGES = { activate: "Chat activated. You can now add it to roles.", deactivate: "Chat deactivated", check: null };

async function act(chat, action) {
	busy.value = chat.name + action;
	const data = await run(api("chat_action", { name: chat.name, action }), MESSAGES[action]);
	busy.value = "";
	if (!data) return;
	if (action === "check") {
		const r = data.result || {};
		r.ok ? toast.success(`Bot can manage ${chat.name}`) : toast.error(r.error || (r.messages || []).join(" ") || "The bot can't manage this chat yet");
	}
	await load();
	loadBoot();
}

function openReject(chat) {
	rejecting.value = chat;
	reason.value = "";
	rejectOpen.value = true;
}

async function reject() {
	const data = await run(api("chat_action", { name: rejecting.value.name, action: "reject", reason: reason.value }), "Chat rejected");
	if (data) {
		rejectOpen.value = false;
		await load();
		loadBoot();
	}
}

watch(tab, load, { immediate: true });
</script>
