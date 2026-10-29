/**
 * 玩家侧入口：按路径决定挂哪个页面（方案 35 的四屏结构）。
 *
 *   `/`            大厅（世界选择）
 *   `/world/<id>`  世界详情（存档槽）
 *   `/setup`       房间（号位 × agent）
 *   `/play`        对局（`?sid=` 指定存档）
 *
 * 说明：`src/` 下的页面是**新的**前端实现，构建产物在 `static/dist/`；
 * 后端 `pages.py` 优先用构建产物，没有产物时回退旧的单文件 HTML（保证不装
 * 依赖也能跑起来）。不引 vue-router：四个路径不值得多一个依赖。
 */
import { createApp } from "vue";

import AgentLibraryPage from "./pages/AgentLibraryPage.vue";
import IndexPage from "./pages/IndexPage.vue";
import LobbyPage from "./pages/LobbyPage.vue";
import SavePage from "./pages/SavePage.vue";
import SetupPage from "./pages/SetupPage.vue";
import WorldAgentsPage from "./pages/WorldAgentsPage.vue";
import WorldPage from "./pages/WorldPage.vue";
import "./styles/theme.css";
import { currentRoute } from "./lib/route.js";

const PAGES = {
  agents: AgentLibraryPage,
  lobby: LobbyPage,
  world: WorldPage,
  saves: SavePage,
  "world-agents": WorldAgentsPage,
  setup: SetupPage,
  play: IndexPage,
};

const route = currentRoute();
createApp(PAGES[route.name] || IndexPage).mount("#app");
