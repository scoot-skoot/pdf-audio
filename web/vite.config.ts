import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/jobs": "http://localhost:8080",
      "/healthz": "http://localhost:8080",
    },
  },
  preview: {
    port: 5173,
  },
});
