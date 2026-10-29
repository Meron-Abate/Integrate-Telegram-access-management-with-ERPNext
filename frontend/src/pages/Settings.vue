<template>
	<PageHeader title="Settings">
		<Button variant="solid" label="Save" :loading="saving" :disabled="!dirty" @click="save" />
	</PageHeader>
	<div v-if="!data" class="flex justify-center py-16"><LoadingIndicator class="h-6 w-6 text-ink-gray-5" /></div>
	<div v-else class="mx-auto max-w-3xl space-y-6 px-6 py-6">
		<section class="rounded-lg border p-5">
			<div class="flex items-start justify-between gap-4">
				<div>
					<h2 class="text-base font-semibold text-ink-gray-9">Bot</h2>
					<p class="mt-1 text-sm" :class="data.token.configured ? 'text-ink-green-3' : 'text-ink-amber-3'">
						{{ data.token.configured ? `Connected${data.bot_username ? " as @" + data.bot_username : ""}` : "No bot token yet" }}
					</p>
				</div>
				<Switch v-model="values.enabled" label="Bot is on" />
			</div>
			<div class="mt-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
				<FormControl
					type="password"
					:label="data.token.configured ? 'Replace bot token' : 'Bot token from @BotFather'"
					v-model="token"
					:placeholder="data.token.configured ? 'Leave empty to keep the current token' : '123456789:ABC…'"
				/>
				<Button label="Test connection" :disabled="!data.token.configured" @click="test" />
			</div>
			<p v-if="data.token.source === 'environment'" class="mt-2 text-sm text-ink-amber-3">
				The server's TELEGRAM_BOT_TOKEN environment variable is used instead of this field.
			</p>
		</section>

		<section class="rounded-lg border p-5">
			<h2 class="text-base font-semibold text-ink-gray-9">How messages reach ERPNext</h2>
			<div class="mt-3 w-64"><FormControl type="select" :options="modes" v-model="values.update_mode" /></div>
			<div v-if="values.update_mode === 'Webhook'" class="mt-4 space-y-3">
				<FormControl label="Site address (HTTPS)" v-model="values.public_base_url" placeholder="https://erp.yourcompany.com" />
				<div class="flex flex-wrap items-center gap-2">
					<Button variant="solid" label="Connect webhook" :disabled="dirty || !data.webhook_url" :loading="connecting" @click="connect" />
					<Button label="Check webhook" :disabled="!data.webhook_url" @click="checkWebhook" />
					<span v-if="dirty" class="text-sm text-ink-gray-5">Save first.</span>
				</div>
				<p v-if="data.webhook_status" class="text-sm text-ink-gray-5">{{ data.webhook_status }}</p>
			</div>
			<p v-else class="mt-3 text-sm text-ink-gray-5">
				For local testing: keep <code class="rounded bg-surface-gray-2 px-1">bench --site &lt;site&gt; telegram-poll</code> running on the server.
				Use Webhook on a live site.
			</p>
		</section>

		<section class="rounded-lg border p-5">
			<h2 class="text-base font-semibold text-ink-gray-9">Groups & channels</h2>
			<div class="mt-3 w-72">
				<FormControl type="select" label="Community for newly registered chats" :options="communityOptions" v-model="values.default_telegram_community" />
			</div>
		</section>

		<section class="rounded-lg border p-5">
			<h2 class="text-base font-semibold text-ink-gray-9">Employees</h2>
			<div class="mt-3 space-y-3">
				<Switch v-model="values.notify_on_approval" label="Message employees when approved" description="Sends their invitation links." />
				<Switch v-model="values.notify_on_rejection" label="Message employees when rejected" />
				<Switch v-model="values.notify_on_revocation" label="Message employees when access is removed" />
				<Switch v-model="values.auto_revoke_on_employee_exit" label="Remove access automatically when an employee leaves" description="Otherwise they are listed under People → Needs attention." />
				<div class="grid gap-3 pt-2 md:grid-cols-2">
					<FormControl type="number" label="Invitation links expire after (hours)" v-model="values.invite_link_expiry_hours" />
					<FormControl label="Help text in bot messages" v-model="values.support_contact" />
				</div>
			</div>
		</section>

		<a href="/app/telegram-access-settings" class="inline-flex items-center gap-1.5 text-sm text-ink-gray-5 hover:text-ink-gray-8">
			<FeatherIcon name="external-link" class="h-3.5 w-3.5" /> Advanced settings (retries, limits, removal rules)
		</a>
	</div>
</template>

<script setup>
import { Button, FeatherIcon, FormControl, LoadingIndicator, Switch, toast } from "frappe-ui";
import { computed, onMounted, ref } from "vue";

import { api, run } from "../api";
import PageHeader from "../components/PageHeader.vue";
import { loadBoot } from "../store";

const CHECKS = ["enabled", "notify_on_approval", "notify_on_rejection", "notify_on_revocation", "auto_revoke_on_employee_exit"];
const modes = [
	{ label: "Polling (local testing)", value: "Polling" },
	{ label: "Webhook (live site)", value: "Webhook" },
];

const data = ref(null);
const values = ref({});
const token = ref("");
const snapshot = ref("");
const saving = ref(false);
const connecting = ref(false);

const communityOptions = computed(() => [{ label: "Not set", value: "" }, ...(data.value?.communities || []).map((c) => ({ label: c, value: c }))]);
const dirty = computed(() => !!token.value || JSON.stringify(values.value) !== snapshot.value);

function apply(d) {
	data.value = d;
	const v = { ...d.values };
	for (const k of CHECKS) v[k] = !!v[k];
	v.default_telegram_community = v.default_telegram_community || "";
	values.value = v;
	snapshot.value = JSON.stringify(v);
	token.value = "";
}

async function save() {
	saving.value = true;
	const payload = { ...values.value };
	for (const k of CHECKS) payload[k] = payload[k] ? 1 : 0;
	const d = await run(api("save_settings", { values: payload, bot_token: token.value || undefined }), "Settings saved");
	saving.value = false;
	if (d) {
		apply(d);
		loadBoot();
	}
}

async function test() {
	const r = await run(api("telegram_access_manager.api.admin.test_connection"));
	if (r) toast.success(`Connected to @${r.username}`);
	apply(await api("get_settings"));
}

async function connect() {
	connecting.value = true;
	const r = await run(api("connect_webhook"), "Webhook connected. Telegram will now send messages to this site.");
	connecting.value = false;
	if (r) apply(await api("get_settings"));
}

async function checkWebhook() {
	const r = await run(api("telegram_access_manager.api.admin.get_webhook_info"));
	if (r) {
		r.last_error_message ? toast.error(`Telegram reports: ${r.last_error_message}`) : toast.success(r.url ? `Webhook OK · ${r.pending_update_count} waiting` : "No webhook is set");
		apply(await api("get_settings"));
	}
}

onMounted(async () => apply(await api("get_settings")));
</script>
