<template>
	<header class="sticky top-0 z-10 flex items-center justify-between gap-4 border-b bg-surface-white px-6 py-3">
		<div class="flex min-w-0 items-center gap-2">
			<RouterLink v-if="back" :to="back" class="text-ink-gray-5 hover:text-ink-gray-8">
				<FeatherIcon name="arrow-left" class="h-4 w-4" />
			</RouterLink>
			<h1 class="truncate text-lg font-semibold text-ink-gray-9">{{ title }}</h1>
			<slot name="badge" />
		</div>
		<div class="flex items-center gap-2">
			<slot />
		</div>
	</header>
</template>

<script setup>
import { FeatherIcon } from "frappe-ui";

defineProps({ title: String, back: String });
</script>
