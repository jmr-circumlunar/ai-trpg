<!--
  存档槽列表（**公共组件**）：1 个自动存档 + 5 个命名存档。

  槽位**由后端给**（`/api/worlds/<id>/saves` 的 `auto` / `slots` / `extra`），
  组件只渲染 + 抛事件，不排布、不存 localStorage —— 槽位规则是后端口径。
-->
<template>
  <div class="save-list">
    <article class="save-slot" :class="{ 'save-empty': !auto }">
      <div class="ss-main">
        <div class="ss-title">
          <span class="slot-tag chip chip-accent">自动</span>
          <span v-if="auto">{{ auto.name || auto.session_id }}</span>
          <span v-else class="muted">还没有自动存档</span>
        </div>
        <div v-if="auto" class="sub">
          第 {{ auto.turns || 0 }} 回合 ·
          {{ auto.status === "ended" ? "已结束" : "进行中" }} ·
          {{ stamp(auto.updated_at) }}
        </div>
      </div>
      <div class="ss-actions">
        <button
          v-if="auto"
          class="btn btn-sm"
          type="button"
          :disabled="auto.status === 'ended'"
          @click="$emit('resume', auto)"
        >
          继续
        </button>
        <button v-else class="btn btn-ghost btn-sm" type="button" @click="$emit('create')">
          开一局
        </button>
      </div>
    </article>

    <article
      v-for="slot in slots"
      :key="slot.index"
      class="save-slot"
      :class="{ 'save-empty': !slot.session }"
    >
      <div class="ss-main">
        <div class="ss-title">
          <span class="slot-tag chip">槽 {{ slot.index }}</span>
          <input
            v-if="slot.session"
            class="field slot-name"
            :value="slot.name"
            @change="$emit('rename', slot.session, $event.target.value)"
          />
          <span v-else class="muted">空</span>
        </div>
        <div v-if="slot.session" class="sub">
          第 {{ slot.session.turns || 0 }} 回合 ·
          {{ slot.session.status === "ended" ? "已结束" : "进行中" }} ·
          {{ stamp(slot.session.updated_at) }}
        </div>
        <div v-else class="sub">这一槽还没有对局</div>
      </div>
      <div class="ss-actions">
        <button
          v-if="slot.session"
          class="btn btn-sm"
          type="button"
          :disabled="slot.session.status === 'ended'"
          @click="$emit('resume', slot.session)"
        >
          继续
        </button>
        <button v-else class="btn btn-ghost btn-sm" type="button" @click="$emit('create')">
          开一局
        </button>
      </div>
    </article>

    <div v-if="extra" class="sub">更早还有 {{ extra }} 局历史（不展开，免得列表又长起来）</div>
  </div>
</template>

<script setup>
defineProps({
  auto: { type: Object, default: null },
  slots: { type: Array, default: () => [] },
  extra: { type: Number, default: 0 },
});

defineEmits(["resume", "rename", "create"]);

function stamp(value) {
  const text = String(value || "");
  return text ? text.slice(0, 16).replace("T", " ") : "—";
}
</script>
