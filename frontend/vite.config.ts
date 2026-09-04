import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 运营侧 API 代理到本地后端（FastAPI :8000）；买家侧当前走 mock 数据层，后端落地后同样走 /api。
export default defineConfig({
  plugins: [react()],
  server: {
    // host: Windows 下 vite 默认只监听 IPv6 的 ::1，配合系统代理（Clash）会导致
    // 127.0.0.1 请求被代理接管后连不上 → 502 / 登录失败。显式监听全部网卡修复。
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      // 凭证图片：后端以 /uploads 静态挂载，前端直接 <img src="/uploads/..."> 需代理过去
      "/uploads": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
