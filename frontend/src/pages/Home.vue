<template>
	<PageHeader title="Home" />
	<div class="mx-auto max-w-5xl space-y-8 px-6 py-6">
		<div v-if="setupNeeded" class="flex items-center justify-between rounded-lg border border-outline-amber-1 bg-surface-amber-1 px-4 py-3">
			<div class="text-base text-ink-amber-3">
				<span class="font-medium">The bot isn't connected yet.</span> Add the bot token in Settings to start.
			</div>
			<Button v-if="boot.is_manager" label="Open Settings" @click="router.push('/settings')" />
		</div>

		<section v-if="data.unlisted?.length" class="rounded-lg border border-outline-amber-1 bg-surface-amber-1 p-4">
			<h2 class="text-base font-semibold text-ink-amber-3">In a chat without access in ERPNext</h2>
			<p class="mb-3 text-sm text-ink-amber-3">These people are in a managed group or channel but were not approved here, for example added by an admin directly in Telegram. Remove them, or keep them.</p>
			<div class="divide-y divide-outline-amber-1 rounded-lg bg-surface-white">
				<div v-for="m in data.unlisted" :key="m.name" class="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
					<div class="min-w-0">
						<div class="truncate text-base font-medium text-ink-gray-8">{{ m.display_name || m.telegram_user_id }}<span v-if="m.telegram_username" class="font-normal text-ink-gray-5"> @{{ m.telegram_username }}</span></div>
						<div class="text-sm text-ink-gray-5">{{ m.chat }} · {{ m.self_joined ? "joined on their own" : m.added_by ? "added by " + m.added_by : "added by an admin" }} · {{ timeAgo(m.joined_on) }}</div>
					</div>
					<div v-if="boot.is_manager" class="flex gap-2">
						<Button label="Keep" @click="unlistedAction(m, 'keep')" />
						<Button theme="red" label="Remove from chat" @click="unlistedAction(m, 'remove')" />
					</div>
				</div>
			</div>
		</section>

		<div class="grid grid-cols-2 gap-3 md:grid-cols-4">
			<button
				v-for="card in cards"
				:key="card.label"
				class="rounded-lg border p-4 text-left transition hover:border-outline-gray-3 hover:shadow-sm"
				@click="router.push(card.to)"
			>
				<div class="text-sm text-ink-gray-5">{{ card.label }}</div>
				<div class="mt-1 text-2xl font-semibold" :class="card.value && card.alert ? 'text-ink-amber-3' : 'text-ink-gray-9'">
					{{ card.value ?? "–" }}
				</div>
			</button>
		</div>

		<div class="grid gap-6 md:grid-cols-2">
			<section>
				<h2 class="mb-2 text-base font-semibold text-ink-gray-8">Requests waiting for you</h2>
				<div class="divide-y rounded-lg border">
					<RouterLink
						v-for="req in data.requests"
						:key="req.name"
						:to="`/requests/${req.name}`"
						class="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-surface-gray-1"
					>
						<div class="min-w-0">
							<div class="truncate text-base font-medium text-ink-gray-8">{{ req.person }}</div>
							<div class="text-sm text-ink-gray-5">{{ req.employee_name || "Employee not linked yet" }} · {{ timeAgo(req.requested_on) }}</div>
						</div>
						<StatusBadge :label="req.status" />
					</RouterLink>
					<div v-if="!data.requests?.length" class="px-4 py-6 text-center text-base text-ink-gray-5">No requests waiting.</div>
				</div>
			</section>

			<section>
				<h2 class="mb-2 text-base font-semibold text-ink-gray-8">Groups & channels to review</h2>
				<div class="divide-y rounded-lg border">
					<RouterLink
						v-for="chat in data.chats"
						:key="chat.name"
						to="/chats?tab=review"
						class="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-surface-gray-1"
					>
						<div class="min-w-0">
							<div class="truncate text-base font-medium text-ink-gray-8">{{ chat.name }}</div>
							<div class="text-sm text-ink-gray-5">{{ chat.type }}<span v-if="chat.registered_by"> · registered by {{ chat.registered_by }}</span></div>
						</div>
						<StatusBadge :label="chat.bot" />
					</RouterLink>
					<div v-if="!data.chats?.length" class="px-4 py-6 text-center text-base text-ink-gray-5">Nothing to review.</div>
				</div>
			</section>
		</div>

		<section>
			<h2 class="mb-2 text-base font-semibold text-ink-gray-8">Recent activity</h2>
			<div class="divide-y rounded-lg border">
				<div v-for="(item, i) in data.activity" :key="i" class="flex items-center gap-3 px-4 py-2">
					<span class="h-2 w-2 shrink-0 rounded-full" :class="item.result === 'Failed' ? 'bg-surface-red-5' : item.result === 'Success' ? 'bg-surface-green-3' : 'bg-surface-gray-4'" />
					<div class="min-w-0 flex-1 truncate text-base text-ink-gray-7">
						<span class="font-medium text-ink-gray-8">{{ item.event }}</span>
						<span v-if="item.subject"> · {{ item.subject }}</span>
					</div>
					<div class="shrink-0 text-sm text-ink-gray-5">{{ item.by }} · {{ timeAgo(item.when) }}</div>
				</div>
				<div v-if="!data.activity?.length" class="px-4 py-6 text-center text-base text-ink-gray-5">No activity yet.</div>
			</div>
		</section>
	</div>
</template>

<script setup>
import { Button } from "frappe-ui";
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api, run, timeAgo } from "../api";
import PageHeader from "../components/PageHeader.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { boot } from "../store";

const router = useRouter();
const data = ref({ counts: {}, requests: [], chats: [], unlisted: [], activity: [] });

const setupNeeded = computed(() => boot.loaded && !boot.bot.configured);
const cards = computed(() => {
	const c = data.value.counts || {};
	return [
		{ label: "Requests waiting", value: c.requests, to: "/requests", alert: true },
		{ label: "People with access", value: c.people, to: "/people" },
		{ label: "Need attention", value: (c.review || 0) + (c.problems || 0), to: "/people?tab=attention", alert: true },
		{ label: "Chats to review", value: c.chats_to_review, to: "/chats?tab=review", alert: true },
	];
});

async function unlistedAction(member, action) {
	const list = await run(api("unlisted_action", { name: member.name, action }), action === "remove" ? "Removed from the chat" : "Kept");
	if (list) data.value = { ...data.value, unlisted: list };
}

onMounted(async () => {
	data.value = await api("get_home");
	boot.counts = data.value.counts;
});
</script>
