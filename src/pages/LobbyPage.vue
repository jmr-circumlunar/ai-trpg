<!--
  大厅（世界选择）：登录后 / 打开站点看到的第一屏。

  一个世界 = 一个「游戏」：卡片上给名字、一句话基调、人数、最近游玩。
  **不显示存档数与布局名**：存档数是历史局数（几百条）会误导，布局是内部骨架名，
  玩家不需要知道。存档在**世界首页**里看（1 自动 + 5 命名）。

  卡片渲染复用公共组件 `WorldCard`（不各写一份）。
-->
<template>
  <div class="shell">
    <header class="topbar">
      <h1>桌上剧场</h1>
      <span class="sub" id="hdr-note">{{ note }}</span>
      <span class="grow"></span>
      <a class="btn btn-ghost btn-sm" href="/agents">Agent 库</a>
      <a class="btn btn-ghost btn-sm" href="/gm">配置</a>
    </header>

    <main class="lobby">
      <div class="lobby-head">
        <div>
          <div class="lobby-title">选一个世界</div>
          <div class="sub">每个世界是一套独立的规则与人物；存档在各自的世界首页里。</div>
        </div>
        <button class="btn btn-ghost" type="button" @click="load">刷新</button>
      </div>

      <div v-if="!worlds.length" class="empty">还没有可用的世界包。</div>

      <div class="world-grid">
        <WorldCard
          v-for="item in worlds"
          :key="item.id"
          :world="item"
          @open="open"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";

import WorldCard from "../components/WorldCard.vue";
import { friendlyError, getJson } from "../lib/api.js";
import { go } from "../lib/route.js";

const worlds = ref([]);
const note = ref("");

onMounted(load);

async function load() {
  try {
    // 大厅要的是**世界摘要列表**（一次拿全，不逐个拉 context —— 避免 N+1）
    const data = await getJson("/api/me/worlds");
    worlds.value = data.worlds || [];
    note.value = "";
  } catch (err) {
    note.value = friendlyError(err);
  }
}

function open(item) {
  go(`/world/${encodeURIComponent(item.id)}`);
}
</script>
