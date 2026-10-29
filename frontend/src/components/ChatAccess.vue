<template>
	<div class="divide-y rounded-lg border">
		<div v-if="!chats.length" class="p-4 text-base text-ink-gray-5">
			No active groups or channels yet. Activate them under Groups & Channels.
		</div>
		<div v-for="chat in chats" :key="chat.chat" class="flex items-center gap-3 px-4 py-2.5">
			<FeatherIcon :name="chat.type === 'Channel' ? 'radio' : 'users'" class="h-4 w-4 shrink-0 text-ink-gray-5" />
			<div class="min-w-0 flex-1">
				<div class="truncate text-base font-medium text-ink-gray-8">{{ chat.chat }}</div>
				<div class="text-sm text-ink-gray-5">
					{{ chat.type }}
					<span v-if="chat.in_role"> · in role</span>
					<span v-if="chat.problem" class="text-ink-red-4"> · {{ chat.problem }}</span>
				</div>
			</div>
			<StatusBadge v-if="showMembership && value[chat.chat]" :label="chat.membership" />
			<Switch v-if="editable" :modelValue="!!value[chat.chat]" @update:modelValue="(on) => toggle(chat.chat, on)" />
			<FeatherIcon v-else :name="value[chat.chat] ? 'check' : 'minus'" class="h-4 w-4" :class="value[chat.chat] ? 'text-ink-green-3' : 'text-ink-gray-4'" />
		</div>
	</div>
</template>

<script setup>
import { FeatherIcon, Switch } from "frappe-ui";

import StatusBadge from "./StatusBadge.vue";

/** One switch per chat. `value` is { chatName: true|false }. */
const props = defineProps({
	chats: { type: Array, default: () => [] },
	value: { type: Object, default: () => ({}) },
	editable: Boolean,
	showMembership: Boolean,
});
const emit = defineEmits(["update:value"]);

function toggle(chat, on) {
	emit("update:value", { ...props.value, [chat]: on });
}
</script>
