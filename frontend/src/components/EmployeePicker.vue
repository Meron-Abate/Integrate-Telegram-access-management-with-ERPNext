<template>
	<div ref="root" class="relative">
		<div class="relative">
			<input
				v-model="text"
				type="text"
				autocomplete="off"
				placeholder="Search employee by name or ID"
				class="w-full rounded bg-surface-gray-2 px-2.5 py-1.5 pr-8 text-base text-ink-gray-8 placeholder-ink-gray-4 focus:outline-none focus:ring-2 focus:ring-outline-gray-3"
				@focus="openList"
				@input="onInput"
			/>
			<button
				v-if="modelValue"
				type="button"
				class="absolute right-2 top-1/2 -translate-y-1/2 text-ink-gray-5 hover:text-ink-gray-8"
				@click="clear"
			>
				<FeatherIcon name="x" class="h-4 w-4" />
			</button>
		</div>
		<ul v-if="open" class="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border bg-surface-white p-1 shadow-lg">
			<li v-if="loading" class="px-2.5 py-2 text-sm text-ink-gray-5">Searching…</li>
			<li v-else-if="!options.length" class="px-2.5 py-2 text-sm text-ink-gray-5">No employees found</li>
			<li
				v-for="o in options"
				:key="o.value"
				class="flex cursor-pointer items-center justify-between gap-3 rounded px-2.5 py-1.5 text-base text-ink-gray-8 hover:bg-surface-gray-2"
				@mousedown.prevent="select(o)"
			>
				<span>{{ o.label }}</span>
				<span class="text-sm text-ink-gray-5">{{ o.description }}</span>
			</li>
		</ul>
	</div>
</template>

<script setup>
import { FeatherIcon, debounce } from "frappe-ui";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

import { api } from "../api";

const props = defineProps({ modelValue: [Object, String, null] });
const emit = defineEmits(["update:modelValue"]);

const root = ref(null);
const text = ref("");
const options = ref([]);
const loading = ref(false);
const open = ref(false);

const labelOf = (v) => (v && typeof v === "object" ? v.label : v || "");

const search = debounce(async (txt) => {
	loading.value = true;
	try {
		const rows = await api("search_employees", { txt });
		options.value = rows.map((e) => ({
			label: e.employee_name,
			value: e.name,
			description: [e.name, e.designation, e.department].filter(Boolean).join(" · "),
		}));
	} finally {
		loading.value = false;
	}
}, 250);

function openList() {
	open.value = true;
	search("");
}

function onInput() {
	open.value = true;
	search(text.value);
}

function select(option) {
	emit("update:modelValue", option);
	text.value = option.label;
	open.value = false;
}

function clear() {
	emit("update:modelValue", null);
	text.value = "";
	search("");
}

function onOutside(event) {
	if (root.value && !root.value.contains(event.target)) {
		open.value = false;
		text.value = labelOf(props.modelValue);
	}
}

watch(() => props.modelValue, (v) => (text.value = labelOf(v)), { immediate: true });
onMounted(() => document.addEventListener("mousedown", onOutside));
onBeforeUnmount(() => document.removeEventListener("mousedown", onOutside));
</script>
