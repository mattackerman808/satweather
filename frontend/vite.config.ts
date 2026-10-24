import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, /api goes to a backend on localhost:8400 (or an ssh tunnel to
// satweather's). In production Apache serves dist/ and proxies /api.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8400" },
  },
});
