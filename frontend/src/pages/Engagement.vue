<template>
	<PageHeader title="Community Engagement">
		<Button v-if="boot.is_manager" label="Preview messages" icon-left="eye" @click="openPreview" />
	</PageHeader>
	<div class="mx-auto max-w-5xl space-y-8 px-6 py-6">
		<div v-if="data.loaded && !data.enabled" class="rounded-lg border border-outline-amber-1 bg-surface-amber-1 px-4 py-3 text-base text-ink-amber-3">
			<span class="font-medium">Weekly engagement is switched off.</span>
			Turn on "Enable Weekly Community Engagement" in Telegram Access Settings to start counting messages and posting.
		</div>

		<section>
			<div class="mb-2 flex items-baseline justify-between">
				<h2 class="text-base font-semibold text-ink-gray-8">Participation this week</h2>
				<div class="text-sm text-ink-gray-5">{{ data.week_start }} to {{ data.week_end }} · "active" means at least one message</div>
			</div>
			<div class="divide-y rounded-lg border">
				<div v-for="d in data.departments" :key="d.chat" class="px-4 py-3">
					<div class="flex flex-wrap items-center justify-between gap-3">
						<div class="min-w-0">
							<div class="truncate text-base font-medium text-ink-gray-8">{{ d.department }}</div>
							<div class="truncate text-sm text-ink-gray-5">{{ d.title }}</div>
						</div>
						<div class="flex items-center gap-5 text-right text-sm text-ink-gray-6">
							<div><div class="text-lg font-semibold text-ink-gray-9">{{ d.active }} / {{ d.eligible }}</div>active people</div>
							<div><div class="text-lg font-semibold text-ink-gray-9">{{ d.total_messages }}</div>messages</div>
							<div><div class="text-lg font-semibold text-ink-gray-9">{{ d.active_days }}</div>active days</div>
							<div><div class="text-lg font-semibold text-ink-gray-9">{{ d.consistent }}</div>on 2+ days</div>
						</div>
					</div>
					<div class="mt-2 flex items-center gap-3">
						<div class="h-2 flex-1 overflow-hidden rounded-full bg-surface-gray-2">
							<div class="h-full rounded-full" :class="d.participation_pct >= data.target_pct ? 'bg-surface-green-3' : 'bg-surface-amber-2'" :style="{ width: d.participation_pct + '%' }" />
						</div>
						<div class="w-12 text-right text-sm font-medium text-ink-gray-8">{{ d.participation_pct }}%</div>
					</div>
					<div v-if="d.most_active.length" class="mt-2 text-sm text-ink-gray-6">
						Most active:
						<span v-for="(p, i) in d.most_active.slice(0, 3)" :key="p.employee">
							<span v-if="i">, </span><span class="font-medium text-ink-gray-8">{{ p.employee_name }}</span> ({{ p.messages }} · {{ p.active_days }} {{ p.active_days === 1 ? "day" : "days" }})
						</span>
					</div>
				</div>
				<div v-if="data.loaded && !data.departments.length" class="px-4 py-6 text-center text-base text-ink-gray-5">
					No department groups yet. A group counts once exactly one "… Team" access role is assigned to it.
				</div>
			</div>
			<p v-if="data.other_groups" class="mt-2 text-sm text-ink-gray-5">
				{{ data.other_groups }} other active {{ data.other_groups === 1 ? "group is" : "groups are" }} left out (shared by several teams, or no team role).
			</p>
		</section>

		<section>
			<h2 class="mb-2 text-base font-semibold text-ink-gray-8">Weekly recognition history</h2>
			<div class="divide-y rounded-lg border">
				<details v-for="h in data.history" :key="h.telegram_chat + h.week_start" class="px-4 py-2">
					<summary class="cursor-pointer text-base text-ink-gray-8">
						<span class="font-medium">{{ h.telegram_chat }}</span>
						<span class="text-sm text-ink-gray-5"> · week of {{ h.week_start }} · {{ timeAgo(h.sent_on) }}</span>
					</summary>
					<pre class="mt-2 whitespace-pre-wrap font-sans text-sm text-ink-gray-7">{{ plain(h.message) }}</pre>
				</details>
				<div v-if="data.loaded && !data.history.length" class="px-4 py-6 text-center text-base text-ink-gray-5">No recognition posts yet. The first one goes out on Friday at 4:00 PM.</div>
			</div>
		</section>
	</div>

	<Dialog v-model="previewOpen" :options="{ title: 'Preview (nothing is sent)', size: '3xl' }">
		<template #body-content>
			<div class="mb-3 flex gap-2">
				<Button v-for="c in ['Monday', 'Wednesday', 'Friday']" :key="c" :label="c" :variant="campaign === c ? 'solid' : 'subtle'" @click="loadPreview(c)" />
			</div>
			<div class="max-h-[60vh] space-y-3 overflow-y-auto">
				<div v-for="p in previews" :key="p.chat" class="rounded-lg border p-3">
					<div class="mb-1 text-sm font-medium text-ink-gray-8">{{ p.department }}</div>
					<pre class="whitespace-pre-wrap font-sans text-sm text-ink-gray-7">{{ p.text ? plain(p.text) : "Nothing would be sent (no eligible members)." }}</pre>
				</div>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog } from "frappe-ui";
import { onMounted, reactive, ref } from "vue";

import { api, run, timeAgo } from "../api";
import PageHeader from "../components/PageHeader.vue";
import { boot } from "../store";

const data = reactive({ loaded: false, enabled: true, departments: [], history: [], target_pct: 50 });
const previewOpen = ref(false);
const campaign = ref("");
const previews = ref([]);

// Telegram messages use a little HTML (bold, mention links); show them as plain text here.
const plain = (html) => String(html || "").replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

async function load() {
	Object.assign(data, await api("get_engagement"), { loaded: true });
}

async function loadPreview(name) {
	campaign.value = name;
	previews.value = (await run(api("preview_engagement", { campaign: name }))) || [];
}

function openPreview() {
	previewOpen.value = true;
	loadPreview("Wednesday");
}

onMounted(load);
</script>
