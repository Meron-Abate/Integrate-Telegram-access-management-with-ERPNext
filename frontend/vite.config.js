import path from "path";
import vue from "@vitejs/plugin-vue";
import frappeui from "frappe-ui/vite";
import { defineConfig } from "vite";

// `yarn build` writes the bundle to ../telegram_access_manager/public/frontend and the page
// template to ../telegram_access_manager/www/telegram-access.html (served at /telegram-access).
export default defineConfig({
	plugins: [
		frappeui({
			frappeProxy: true,
			lucideIcons: true, // frappe-ui components import ~icons/lucide/*
			jinjaBootData: true,
			buildConfig: {
				outDir: path.resolve(__dirname, "../telegram_access_manager/public/frontend"),
				baseUrl: "/assets/telegram_access_manager/frontend/",
				indexHtmlPath: path.resolve(__dirname, "../telegram_access_manager/www/telegram-access.html"),
			},
		}),
		vue(),
	],
	resolve: { alias: { "@": path.resolve(__dirname, "src") } },
	optimizeDeps: { include: ["feather-icons", "showdown", "engine.io-client"] },
});
