<template>
	<FrappeUIProvider>
		<div class="flex h-screen bg-surface-white text-ink-gray-8">
			<aside class="flex w-60 shrink-0 flex-col border-r bg-surface-menu-bar px-2 py-2">
				<Dropdown :options="appOptions" class="w-full">
					<button class="flex w-full items-center gap-2 rounded-md p-1.5 text-left hover:bg-surface-gray-2">
						<img :src="logo" class="h-8 w-8 rounded-md" alt="" />
						<div class="flex-1 leading-tight">
							<div class="text-base font-semibold text-ink-gray-9">Telegram Access</div>
							<div class="text-xs text-ink-gray-5">ERPNext</div>
						</div>
						<FeatherIcon name="chevron-down" class="h-4 w-4 text-ink-gray-6" />
					</button>
				</Dropdown>
				<button
					class="mt-2 flex w-full items-center gap-2 rounded-md bg-surface-gray-2 px-2 py-1.5 text-sm text-ink-gray-6"
					@click="openDeskSearch"
				>
					<FeatherIcon name="search" class="h-4 w-4" />
					<span class="flex-1 text-left">Search</span>
					<span class="text-xs text-ink-gray-5">Ctrl+K</span>
				</button>
				<a href="/desk" class="mt-1 flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-ink-gray-7 hover:bg-surface-gray-2">
					<FeatherIcon name="bell" class="h-4 w-4" /> Notification
				</a>
				<nav class="mt-3 flex flex-1 flex-col gap-0.5">
					<RouterLink
						v-for="item in navItems"
						:key="item.to"
						:to="item.to"
						class="flex items-center gap-2.5 rounded px-2 py-1.5 text-base text-ink-gray-7 hover:bg-surface-gray-2"
						:class="isActive(item.to) ? 'bg-surface-white font-medium text-ink-gray-9 shadow-sm' : ''"
					>
						<FeatherIcon :name="item.icon" class="h-4 w-4" />
						<span class="flex-1">{{ item.label }}</span>
						<span v-if="item.count" class="rounded-full bg-surface-amber-2 px-1.5 text-xs font-medium text-ink-amber-3">
							{{ item.count }}
						</span>
					</RouterLink>
				</nav>
				<a href="/desk/getting-started" class="flex items-center gap-2 rounded px-2 py-1.5 text-sm text-ink-gray-7 hover:bg-surface-gray-2">
					<FeatherIcon name="user-check" class="h-4 w-4" /> Getting Started
				</a>
				<Dropdown :options="userOptions" class="mt-1 w-full">
					<button class="flex w-full items-center gap-2 rounded-md p-1.5 text-left hover:bg-surface-gray-2">
						<span class="flex h-7 w-7 items-center justify-center rounded-full bg-surface-green-2 text-sm text-ink-green-3">
							{{ (boot.user.full_name || "?")[0] }}
						</span>
						<div class="min-w-0 leading-tight">
							<div class="truncate text-sm text-ink-gray-8">{{ boot.user.full_name }}</div>
							<div class="truncate text-xs text-ink-gray-5">{{ boot.user.name }}</div>
						</div>
					</button>
				</Dropdown>
			</aside>

			<main class="flex-1 overflow-y-auto">
				<div v-if="!boot.loaded" class="flex h-full items-center justify-center">
					<LoadingIndicator class="h-6 w-6 text-ink-gray-5" />
				</div>
				<div v-else-if="boot.error" class="flex h-full items-center justify-center p-8 text-center text-ink-gray-6">
					<div>
						<div class="text-lg font-medium text-ink-gray-8">You can't use Telegram Access</div>
						<p class="mt-2">Ask an administrator for the Telegram Access Manager role.</p>
					</div>
				</div>
				<RouterView v-else :key="$route.fullPath" />
			</main>
		</div>
	</FrappeUIProvider>
</template>

<script setup>
import { Dropdown, FeatherIcon, FrappeUIProvider, LoadingIndicator, call } from "frappe-ui";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import { boot, loadBoot } from "./store";

const route = useRoute();
const logo = "/assets/telegram_access_manager/images/telegram-access.svg"; // served by Frappe, not bundled

const navItems = computed(() => {
	const items = [
		{ label: "Home", to: "/", icon: "home" },
		{ label: "Requests", to: "/requests", icon: "inbox", count: boot.counts.requests },
		{ label: "People", to: "/people", icon: "users", count: boot.counts.review + boot.counts.problems },
		{ label: "Groups & Channels", to: "/chats", icon: "message-circle", count: boot.counts.chats_to_review },
		{ label: "Access Roles", to: "/roles", icon: "layers" },
	];
	if (boot.is_manager) items.push({ label: "Settings", to: "/settings", icon: "settings" });
	return items;
});

function isActive(to) {
	return to === "/" ? route.path === "/" : route.path.startsWith(to);
}

const apps = ref([]);
const go = (href) => () => (window.location.href = href);
const appOptions = computed(() => [
	{
		group: "",
		items: [
			{ label: "Desktop", icon: "home", onClick: go("/desk") },
			{
				label: "Workspaces",
				icon: "monitor",
				submenu: apps.value.map((a) => ({ label: a.title, onClick: go(a.route) })),
			},
			{ label: "Website", icon: "globe", onClick: go("/") },
		],
	},
	{
		group: "",
		items: [
			{ label: "Reload", icon: "rotate-cw", onClick: () => window.location.reload() },
			{ label: "Toggle Theme", icon: "moon", onClick: toggleTheme },
			{
				label: "Help",
				icon: "info",
				submenu: [
					{ label: "Frappe Support", onClick: () => window.open("https://support.frappe.io", "_blank") },
					{ label: "Documentation", onClick: () => window.open("https://docs.frappe.io", "_blank") },
				],
			},
		],
	},
	{ group: "", items: [{ label: "Logout", icon: "log-out", onClick: go("/?cmd=web_logout") }] },
]);

function toggleTheme() {
	const root = document.documentElement;
	const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
	root.setAttribute("data-theme", next);
	try {
		localStorage.setItem("tam-theme", next);
	} catch {}
}
const userOptions = [
	{ label: "My Settings", onClick: () => (window.location.href = "/desk/user/" + encodeURIComponent(boot.user.name)) },
	{ label: "Log out", onClick: () => (window.location.href = "/?cmd=web_logout") },
];

function openDeskSearch() {
	window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
}

onMounted(async () => {
	try {
		const saved = localStorage.getItem("tam-theme");
		if (saved) document.documentElement.setAttribute("data-theme", saved);
	} catch {}
	loadBoot();
	try {
		apps.value = await call("frappe.apps.get_apps");
	} catch {
		apps.value = [];
	}
});
</script>
