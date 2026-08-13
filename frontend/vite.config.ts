import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 后端只读 API 默认地址（dev 下通过代理转发；客户端也可用 VITE_API_BASE_URL 直连）
const API_TARGET = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ["echarts"],
          vendor: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"],
        },
      },
    },
  },
  server: {
    port: 4016,
    proxy: {
      // 同源开发时客户端请求 /api/*，代理到后端 /*（去掉 /api 前缀）。
      // 必须用 /api 前缀命名空间：SPA 路由（/projects、/insights 等）与后端路径同名，
      // 直接按路径代理会把前端路由也转发给后端。
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
