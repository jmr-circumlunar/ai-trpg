<!--
  世界首页：进入某个世界后的第一屏。

  三件事就三个按钮：新开一局 / 读取存档 / 退出；下面一块「本世界的 Agent」。

  **渲染所需的世界侧数据全部来自世界上下文**（`/api/worlds/<id>/context`）：
  hero 背景、主题、牌面清单、agent 与经验——页面不自己拼路径、不自己取数。
-->
<template>
  <div class="shell">
    <header class="topbar">
      <a class="btn btn-ghost btn-sm" href="/">← 大厅</a>
      <span class="grow"></span>
      <span v-if="context && context.players && context.players.default" class="sub">
        {{ context.players.default }} 人
      </span>
    </header>

    <main class="world-home">
      <div class="hero" :style="heroStyle">
        <div class="hero-mask"></div>
        <div class="hero-body">
          <h1 class="hero-title">{{ worldName }}</h1>
          <p class="hero-tone">{{ worldTone }}</p>

          <div class="hero-actions">
            <button class="btn btn-lg" type="button" @click="newGame">新开一局</button>
            <a class="btn btn-ghost btn-lg" :href="`/world/${worldId}/saves`">读取存档</a>
            <a class="btn btn-ghost btn-lg" href="/">退出</a>
          </div>

          <div class="hero-meta">
            <span v-if="saveCount">{{ saveCount }} 个存档</span>
          </div>
        </div>
      </div>

      <!-- 本世界的 Agent：谁在这个世界上场过、攒了多少经验（世界级信息） -->
      <div class="world-agents">
        <div class="lobby-head">
          <div>
            <div class="lobby-title">本世界的 Agent</div>
            <div class="sub">这些人在这个世界打过仗，攒下了经验。</div>
          </div>
          <a class="btn btn-ghost btn-sm" :href="`/world/${worldId}/agents`">配置参战名单</a>
          <a class="btn btn-ghost btn-sm" href="/agents">去 Agent 库</a>
        </div>

        <div v-if="!agents.length" class="empty">这个世界还没有人上场。</div>

        <div class="agent-grid">
          <article v-for="agent in agents" :key="agent.id" class="agent-item">
            <div class="ai-top">
              <span class="ai-name">{{ agent.name }}</span>
              <span class="grow"></span>
              <span class="chip">{{ agent.experience }} 条经验</span>
            </div>
            <p class="ai-persona">{{ agent.persona || "（还没写性格）" }}</p>
          </article>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";

import { getJson } from "../lib/api.js";
import { go } from "../lib/route.js";
import { loadWorldTheme } from "../lib/theme.js";
import { loadContext } from "../lib/world-context.js";

const worldId = decodeURIComponent(location.pathname.split("/")[2] || "");
const context = ref(null);
const saveCount = ref(0);

const worldName = computed(() => (context.value?.world?.name) || "");
const worldTone = computed(() => (context.value?.world?.tone) || "");
const agents = computed(() => context.value?.agents || []);

// hero 背景图由上下文给（有就用，没有就回落主题渐变）
const heroStyle = computed(() => {
  const url = context.value?.ui?.assets?.hero;
  return url
    ? { backgroundImage: `url(${url})` }
    : { backgroundImage: "linear-gradient(160deg, #2a1417 0%, #12070a 60%, #0a0406 100%)" };
});

onMounted(async () => {
  try {
    context.value = await loadContext(worldId);
    if (context.value?.ui?.theme_url) loadWorldTheme(context.value.ui.theme_url);
  } catch {
    context.value = null;
  }
  try {
    const data = await getJson(`/api/worlds/${encodeURIComponent(worldId)}/saves`);
    saveCount.value = (data.saves || []).length;
  } catch {
    saveCount.value = 0;
  }
});

function newGame() {
  go(`/setup?world=${encodeURIComponent(worldId)}`);
}
</script>
