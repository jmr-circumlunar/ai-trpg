import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

/**
 * 前端构建配置。
 *
 * - 产物输出到 `static/dist/`（`pages.py` 按存在性选用：没有构建产物时回退旧静态页）
 * - `base: "/static/"`：产物由后端的 `/static/{name}` 路由提供，路径前缀要带上
 * - 源码在 `src/`，测试断言的是**源码**里的元素 id，因此不装依赖也能跑 CI
 */
export default defineConfig({
  // `root: "src"`：入口页在 src/ 下，产物直接是 `dist/index.html`（不带 src/ 层级）
  root: "src",
  plugins: [vue()],
  // 产物在 `static/dist/`，所以资源前缀也是 `/static/dist/`
  // （路由 `/static/{name:path}` 允许子目录，见 `api/routes/pages.py`）
  base: "/static/dist/",
  build: {
    outDir: "../static/dist",
    emptyOutDir: true,
    assetsDir: "assets",
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL("./src/index.html", import.meta.url)),
        gm: fileURLToPath(new URL("./src/gm.html", import.meta.url)),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
