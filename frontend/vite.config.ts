import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// В dev-режиме (npm run dev) фронт стучится на localhost:5173, а запросы к /api
// проксируются на бэкенд localhost:8000 — так обходимся без CORS-настроек.
// В docker/prod проксирование делает nginx (см. nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
