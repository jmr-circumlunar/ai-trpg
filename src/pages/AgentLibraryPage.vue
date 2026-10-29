<!--
  Agent 库（全局）：**agent 就是「人」**——名字、性格、说话风格。

  归属原则（方案 36）：「agent 是什么」是全局的，在**选世界之前**配好；
  「agent 在某个世界积累了什么」是世界级的，放在世界首页。

  这里改的就是 prompt 里的人格：性格进「怎么想」，说话风格进「怎么说」。
-->
<template>
  <div class="shell">
    <header class="topbar">
      <a class="btn btn-ghost btn-sm" href="/">← 大厅</a>
      <h1>Agent 库</h1>
      <span class="sub">{{ agents.length }} 个人</span>
      <span class="grow"></span>
      <button class="btn" type="button" @click="startNew">新建</button>
    </header>

    <main class="lobby">
      <div v-if="note" class="note">{{ note }}</div>

      <div class="agent-grid">
        <article v-for="agent in agents" :key="agent.id" class="agent-item">
          <div class="ai-top">
            <span class="ai-name">{{ agent.name }}</span>
            <span class="grow"></span>
            <button class="btn btn-ghost btn-sm" type="button" @click="edit(agent)">改</button>
            <button class="btn btn-ghost btn-sm" type="button" @click="remove(agent)">删</button>
          </div>
          <p class="ai-persona">{{ agent.persona || "（还没写性格）" }}</p>
          <p v-if="agent.voice" class="ai-voice">{{ agent.voice }}</p>
        </article>
      </div>

      <div v-if="!agents.length" class="empty">
        还没有 agent。点右上角「新建」造一个——它会出现在所有世界的号位里。
      </div>
    </main>

    <div v-if="editing" class="modal-mask" @click.self="editing = null">
      <div class="modal">
        <h2>{{ editing.id ? "改这个人" : "新建一个人" }}</h2>
        <label class="sub label">名字
          <input id="agent-name" v-model="editing.name" class="field" style="width: 100%" />
        </label>
        <label class="sub label">性格（进 prompt，决定「怎么想」）
          <textarea id="agent-persona" v-model="editing.persona" class="field" rows="3" style="width: 100%"></textarea>
        </label>
        <label class="sub label">说话风格（进 prompt，决定「怎么说」）
          <input id="agent-voice" v-model="editing.voice" class="field" style="width: 100%" />
        </label>
        <div class="modal-actions">
          <button class="btn btn-ghost" type="button" @click="editing = null">取消</button>
          <button class="btn" type="button" @click="save">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";

import { friendlyError, getJson, sendJson } from "../lib/api.js";

const agents = ref([]);
const note = ref("");
const editing = ref(null);

onMounted(load);

async function load() {
  try {
    const data = await getJson("/api/agents");
    agents.value = data.agents || [];
  } catch (err) {
    note.value = friendlyError(err);
  }
}

function startNew() {
  editing.value = { id: "", name: "", persona: "", voice: "" };
}

function edit(agent) {
  editing.value = {
    id: agent.id,
    name: agent.name || "",
    persona: agent.persona || "",
    voice: agent.voice || "",
  };
}

async function save() {
  const payload = {
    name: (editing.value.name || "").trim(),
    persona: editing.value.persona || "",
    voice: editing.value.voice || "",
  };
  if (!payload.name) {
    note.value = "得有个名字";
    return;
  }
  try {
    if (editing.value.id) {
      await sendJson(`/api/agents/${editing.value.id}`, payload, "PUT");
    } else {
      await postJson("/api/agents", payload);
    }
    editing.value = null;
    note.value = "已保存";
    await load();
  } catch (err) {
    note.value = friendlyError(err);
  }
}

async function remove(agent) {
  if (!window.confirm(`删掉「${agent.name}」？`)) return;
  try {
    await sendJson(`/api/agents/${agent.id}`, {}, "DELETE");
    await load();
  } catch (err) {
    note.value = friendlyError(err);
  }
}
</script>

