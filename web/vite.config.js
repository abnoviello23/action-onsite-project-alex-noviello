import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API is a separate origin in dev. Proxying rather than calling it directly
// keeps the browser on one origin, so the app works whether or not the API's
// CORS allowlist happens to include the port Vite picked.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
