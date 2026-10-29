import "./index.css";

import { frappeRequest, resourcesPlugin, setConfig } from "frappe-ui";
import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";

setConfig("resourceFetcher", frappeRequest);

createApp(App).use(router).use(resourcesPlugin).mount("#app");
