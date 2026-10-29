import { createRouter, createWebHistory } from "vue-router";

const routes = [
	{ path: "/", name: "Home", component: () => import("./pages/Home.vue") },
	{ path: "/requests", name: "Requests", component: () => import("./pages/Requests.vue") },
	{ path: "/requests/:name", name: "Request", component: () => import("./pages/RequestDetail.vue"), props: true },
	{ path: "/people", name: "People", component: () => import("./pages/People.vue") },
	{ path: "/people/:name", name: "Person", component: () => import("./pages/PersonDetail.vue"), props: true },
	{ path: "/chats", name: "Chats", component: () => import("./pages/Chats.vue") },
	{ path: "/roles", name: "Roles", component: () => import("./pages/Roles.vue") },
	{ path: "/settings", name: "Settings", component: () => import("./pages/Settings.vue") },
	{ path: "/:pathMatch(.*)*", redirect: "/" },
];

export default createRouter({ history: createWebHistory("/telegram-access"), routes });
