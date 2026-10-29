<!--
  本世界 Agent 配置页（方案 38）：**只管「谁在这个世界上场」**。

  - 人格（名字 / 性格 / 说话风格）的唯一来源是**全局 Agent 库**（右上角「Agent 库」去改）；
  - 这里只维护本世界的**参战名单**（勾选 + 保存，落 `world_agents` 表）；
  - 世界首页只**只读**展示名单与各自在本世界积累的经验。
-->
<template>
  <div class="shell">
    <header class="topbar">
      <a class="btn btn-ghost btn-sm" :href="`/world/${worldId}`">← 返回</a>
      <h1>本世界的 Agent</h1>
      <span class="sub" id="hdr-note">{{ note }}</span>
      <span class="grow"></span>
      <a class="btn btn-ghost btn-sm" href="/agents">Agent 库（改人格）</a>
      <button class="btn" type="button" :disabled="busy" @click="save">保存名单</button>
    </header>

    <main class="lobby">
      <div class="lobby-head">
        <div>
          <div class="lobby-title">{{ worldName }} · 参战名单</div>
          <div class="sub">
            勾选谁在这个世界上场（房间页铺号位时优先用这份名单）。性格与说话风格在 Agent 库里改。
          </div>
        </div>
      </div>

      <div v-if="!available.length" class="empty">
        还没有可用的 agent——先去「Agent 库」造几个。
      </div>

      <div class="agent-grid">
        <label v-for="agent in available" :key="agent.id" class="agent-item pick">
          <input v-model="picked" type="checkbox" :value="agent.id" />
          <div class="ai-top">
            <span class="ai-name">{{ agent.name }}</span>
            <span class="grow"></span>
            <span class="chip">{{ experienceOf(agent.id) }} 条经验</span>
          </div>
          <p class="ai-persona">{{ agent.persona || "（还没写性格）" }}</p>
          <p v-if="agent.voice" class="ai-voice">{{ agent.voice }}</p>
        </label>
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";

import { friendlyError, getJson, sendJson } from "../lib/api.js";
import { loadContext } from "../lib/world-context.js";

const worldId = decodeURIComponent(location.pathname.split("/")[2] || "");
const worldName = ref("");
const available = ref([]);
const picked = ref([]);
const used = ref([]);
const note = ref("");
const busy = ref(false);

onMounted(async () => {
  try {
    const ctx = await loadContext(worldId);
    worldName.value = ctx?.world?.name || "";
  } catch {
    worldName.value = "";
  }
  try {
    const data = await getJson(`/api/worlds/${encodeURIComponent(worldId)}/agents`);
    available.value = data.available || [];
    picked.value = (data.roster || []).map((item) => item.id);
    used.value = data.agents || [];
  } catch (err) {
    note.value = friendlyError(err);
  }
});

function experienceOf(agentId) {
  const hit = used.value.find((item) => item.id === agentId);
  return hit ? hit.experience : 0;
}

async function save() {
  busy.value = true;
  try {
    await sendJson(
      `/api/worlds/${encodeURIComponent(worldId)}/agents`,
      { agent_ids: picked.value },
      "PUT"
    );
    note.value = `已保存名单（${picked.value.length} 人）`;
  } catch (err) {
    note.value = friendlyError(err);
  } finally {
    busy.value = false;
  }
}
</script>
