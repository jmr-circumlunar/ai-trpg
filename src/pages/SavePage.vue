<!--
  存档页：**1 个自动存档 + 5 个命名存档**。

  槽位由后端给（`auto` / `slots` / `extra`），渲染复用公共组件 `SaveSlotList`；
  改名落库（`PATCH /api/sessions/{sid}`）——不再用浏览器本地存（换设备就没了）。
-->
<template>
  <div class="shell">
    <header class="topbar">
      <a class="btn btn-ghost btn-sm" :href="`/world/${worldId}`">← 返回</a>
      <h1>存档</h1>
      <span class="grow"></span>
      <button class="btn" type="button" @click="newGame">新开一局</button>
    </header>

    <main class="lobby">
      <div v-if="note" class="note">{{ note }}</div>

      <SaveSlotList
        :auto="auto"
        :slots="slots"
        :extra="extra"
        @resume="resume"
        @rename="rename"
        @create="newGame"
      />
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";

import SaveSlotList from "../components/SaveSlotList.vue";
import { friendlyError, getJson, sendJson } from "../lib/api.js";
import { go, query } from "../lib/route.js";

const worldId = decodeURIComponent(location.pathname.split("/")[2] || "") || query("world");
const auto = ref(null);
const slots = ref([]);
const extra = ref(0);
const note = ref("");

onMounted(async () => {
  try {
    const data = await getJson(`/api/worlds/${encodeURIComponent(worldId)}/saves`);
    auto.value = data.auto || null;
    slots.value = data.slots || [];
    extra.value = Number(data.extra || 0);
  } catch (err) {
    note.value = friendlyError(err);
  }
});

async function rename(session, name) {
  const clean = String(name || "").slice(0, 24);
  if (!clean) return;
  try {
    await sendJson(`/api/sessions/${encodeURIComponent(session.session_id)}`, { name: clean }, "PATCH");
    note.value = `已改名：${clean}`;
    await load();
  } catch (err) {
    note.value = friendlyError(err);
  }
}

async function load() {
  const data = await getJson(`/api/worlds/${encodeURIComponent(worldId)}/saves`);
  auto.value = data.auto || null;
  slots.value = data.slots || [];
  extra.value = Number(data.extra || 0);
}

function resume(session) {
  go(`/play?sid=${encodeURIComponent(session.session_id)}`);
}

function newGame() {
  go(`/setup?world=${encodeURIComponent(worldId)}`);
}
</script>
