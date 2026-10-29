<!--
  人类席位面板。

  **契约**：这里有 13 个元素 id 是前后端契约（`tests/test_web_ui.py::INDEX_IDS`），
  改名会让测试红，也会让老页面/别的客户端失效。改这里之前先看那个测试。
-->
<template>
  <div
    v-if="panel"
    id="human-panel"
    class="panel"
    style="margin-bottom: 14px"
  >
    <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
      <b id="hp-who">{{ panel.who }}</b>
      <span class="sub" id="hp-countdown">{{ countdown }}</span>
      <span style="flex: 1"></span>
      <span class="sub" id="hp-state">{{ panel.state }}</span>
      <button id="hp-ghost" class="btn btn-ghost btn-sm" type="button" @click="$emit('ghost')">
        交给 AI
      </button>
    </div>

    <div class="sub" id="hp-briefs" style="margin-top: 8px">{{ panel.briefs }}</div>

    <!-- S4-3 ①：当前可见信息（自己的身份 / 私有笔记 / 状态） -->
    <div class="sub" id="hp-visible" style="margin-top: 4px" v-if="visibleText">
      {{ visibleText }}
    </div>

    <!-- S4-3 ②：带理由的建议 —— **只填入输入框，不代替人决策** -->
    <div id="hp-suggestions" style="margin-top: 6px" v-if="panel.suggestions && panel.suggestions.length">
      <div class="sub" style="margin-bottom: 2px">建议（仅供参考，采不采纳由你决定）</div>
      <button
        v-for="item in panel.suggestions"
        :key="item.target"
        class="btn btn-ghost btn-sm suggestion-item"
        type="button"
        :title="item.reason"
        @click="applySuggestion(item)"
      >
        {{ item.label || item.target }} · {{ Math.round((item.confidence || 0) * 100) }}%
        <span class="sub">（{{ item.reason }}）</span>
      </button>
    </div>

    <div class="sub" id="hp-enemies" style="margin-top: 4px" v-if="showCombat">
      <span v-if="!panel.enemies || !panel.enemies.length">（没有敌人）</span>
      <span v-for="enemy in panel.enemies" :key="enemy.id">
        {{ enemy.name }}<span class="muted">·{{ enemy.id }}</span>
        <span v-if="enemy.hp !== undefined" class="muted"> · HP {{ enemy.hp }}</span>
      </span>
    </div>

    <input
      id="hp-thought"
      v-model="thought"
      class="field"
      style="margin-top: 10px"
      placeholder="心里在想什么（别人看不到）"
      maxlength="40"
    />

    <textarea
      id="hp-action"
      v-model="action"
      class="field"
      style="margin-top: 8px"
      placeholder="要做什么？写具体动作"
      maxlength="60"
    ></textarea>

    <div style="display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap">
      <select id="hp-skill" v-model="skill" class="field" style="width: auto">
        <option value="">（不指定技能）</option>
        <option v-for="item in panel.skills" :key="item.name" :value="item.name">
          {{ item.name }}<template v-if="item.value !== undefined">（{{ item.value }}）</template>
        </option>
      </select>

      <select id="hp-intent" v-model="intent" class="field" style="width: auto">
        <option value="normal">normal</option>
        <option value="careful">careful</option>
        <option value="all_in">all_in</option>
      </select>

      <select id="hp-mode" v-model="mode" class="field" style="width: auto" v-if="showCombat">
        <option value="">（不战斗）</option>
        <option value="attack">attack</option>
        <option value="defend">defend</option>
        <option value="flee">flee</option>
      </select>

      <select id="hp-target" v-model="target" class="field" style="width: auto" v-if="showCombat">
        <option value="">（无目标）</option>
        <option v-for="enemy in panel.enemies || []" :key="enemy.id" :value="enemy.id">
          {{ enemy.name }}
        </option>
      </select>

      <button id="hp-submit" class="btn" type="button" :disabled="submitting" @click="$emit('submit')">
        {{ submitting ? "已提交" : "提交" }}
      </button>
    </div>

    <div class="sub" id="hp-hint" style="margin-top: 6px">Ctrl+Enter 提交 · Esc 交给 AI</div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  panel: { type: Object, default: null },
  countdown: { type: String, default: "" },
  submitting: { type: Boolean, default: false },
  showCombat: { type: Boolean, default: true },
});

// S4-3 ①：当前可见信息（身份 / 私有笔记 / 状态）——一句话说清「你现在知道什么」
const visibleText = computed(() => {
  const visible = (props.panel && props.panel.visible) || {};
  const parts = [];
  const identity = visible.identity || {};
  if (identity.name) parts.push(`你是 ${identity.name}`);
  if (identity.profession) parts.push(`身份：${identity.profession}`);
  if (visible.notes && visible.notes.length) parts.push(`已知：${visible.notes.join("；")}`);
  if (visible.conditions && visible.conditions.length) parts.push(`状态：${visible.conditions.join("、")}`);
  return parts.join(" · ");
});

/**
 * S4-3 ②：采纳一条建议 → **只把依据填进输入框**，绝不自动提交。
 * 建议代替人决策 = 玩家在替自己思考（协作游戏设计里明确反对这种做法）。
 */
function applySuggestion(item) {
  const reason = String((item && item.reason) || "").trim();
  if (!reason) return;
  action.value = reason.slice(0, 60);
}

const thought = ref("");
const action = ref("");
const skill = ref("");
const intent = ref("normal");
const mode = ref("");
const target = ref("");

// 每次轮到自己就清空上一次的输入
watch(
  () => props.panel,
  (next) => {
    if (!next) return;
    thought.value = "";
    action.value = "";
    skill.value = "";
    intent.value = "normal";
    mode.value = "";
    target.value = "";
  }
);

defineExpose({
  getPayload() {
    return {
      action: action.value,
      thought: thought.value,
      skill: skill.value,
      intent: intent.value,
      mode: mode.value,
      target: target.value,
    };
  },
  setAction(text) {
    action.value = text;
  },
});
</script>
