<!--
  GM 控制台（Vue3 版）。

  **契约**：本页有 4 个元素 id 与测试绑定（`tests/test_web_ui.py::GM_IDS`）：
  `timeline` / `timeline-hint` / `human-slots` / `inherit`。
-->
<template>
  <div class="shell">
    <header class="topbar">
      <h1>GM 控制台</h1>
      <span class="grow"></span>
      <a class="btn btn-ghost btn-sm" href="/">← 回游戏</a>
    </header>

    <main class="layout" style="grid-template-columns: minmax(0, 1fr) 320px">
      <section class="panel">
        <h2>时间线</h2>
        <div id="timeline">
          <div v-if="!entries.length" class="empty">还没有回合记录</div>
          <div v-for="entry in entries" :key="entry.turn" class="turn">
            <div class="hdr">第 {{ entry.turn }} 回合</div>
            <div class="scene">{{ entry.summary || entry.scene || "" }}</div>
            <div v-for="(actor, index) in entry.actors || []" :key="index" class="act">
              <div class="who">{{ actor.name }}</div>
              <p class="do">{{ actor.action }}</p>
            </div>
          </div>
        </div>
        <div id="timeline-hint" class="sub" style="margin-top: 10px">
          时间线来自后端落库的回合记录；点复盘页可以看完整轨迹。
        </div>
      </section>

      <aside class="panel">
        <h2>人类席位</h2>
        <div id="human-slots">
          <div v-if="!slots.length" class="empty">没有等待中的席位</div>
          <div v-for="slot in slots" :key="slot.char_id" class="card">
            <div class="name">{{ slot.name }}</div>
            <div class="meta">第 {{ slot.turn }} 回合 · 剩余 {{ slot.remaining_ms }}ms</div>
          </div>
        </div>

        <h2 style="margin-top: 18px">继承</h2>
        <button id="inherit" class="btn btn-ghost" type="button" @click="inheritFrom">
          继承上一局
        </button>
        <div class="sub" style="margin-top: 6px">
          新开一局并带上上一局留下的传闻（局外传承）。
        </div>
        <div v-if="note" class="sub" style="margin-top: 8px">{{ note }}</div>
      </aside>
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";

import { friendlyError, getJson, postJson } from "../lib/api.js";

const sid = ref(new URLSearchParams(location.search).get("sid") || "");
const entries = ref([]);
const slots = ref([]);
const note = ref("");

onMounted(async () => {
  if (!sid.value) return;
  await Promise.all([loadTrace(), loadPending()]);
});

async function loadTrace() {
  try {
    const data = await getJson(`/api/sessions/${sid.value}/trace`);
    entries.value = data.turns || data.entries || [];
  } catch (err) {
    note.value = friendlyError(err);
  }
}

async function loadPending() {
  try {
    const data = await getJson(`/api/sessions/${sid.value}/pending`);
    slots.value = data.pending ? [data.pending] : [];
  } catch (err) {
    note.value = friendlyError(err);
  }
}

async function inheritFrom() {
  if (!sid.value) {
    note.value = "先在游戏页开一局";
    return;
  }
  try {
    const data = await postJson("/api/sessions", { inherit_from: sid.value });
    note.value = `已继承：新局 ${data.session_id || ""}`;
  } catch (err) {
    note.value = friendlyError(err);
  }
}
</script>
