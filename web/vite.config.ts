import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: /api/* -> FastAPI (mặc định localhost:8000). Production: nginx (web/nginx.conf) proxy y hệt (cùng origin, SSE không buffer).
// VITE_BASE: đường dẫn gốc khi phục vụ dưới thư mục con (vd /app/ trên Vercel cùng trang game). Mặc định /.
export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
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
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          vega: ["vega", "vega-lite", "vega-embed"],
          graph: ["react-force-graph-2d"],
        },
      },
    },
  },
});
