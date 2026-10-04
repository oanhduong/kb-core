import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: /api/* -> API FastAPI (mặc định localhost:8000). Production: nginx (chat_ui/nginx.conf) proxy y hệt.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
