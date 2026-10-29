<!--
  角色卡（**原子件**）：牌面图 + 身份 + 阵营 + 存活 + 资源 + 已确认信息。

  复用优先：牌桌行、其它视图都直接用它。
  牌面图地址来自**世界上下文的资产清单**（`cardUrls`），前端不拼路径、不认世界语义；
  身份/阵营显示名同样由世界包声明。
-->
<template>
  <div class="seat-card" :class="{ 'seat-out': !agent.alive }" :data-art="cardImage">
    <img v-if="cardImage" class="seat-art" :src="cardImage" alt="" />
    <!-- 标头可由调用方接管（插槽化座位把它提到行级 `header` 槽，避免一行两个标题） -->
    <div v-if="showHead" class="seat-card-top">
      <span class="seat-no">{{ no }}</span>
      <span class="seat-name">{{ agent.name }}</span>
      <span class="seat-role">{{ agent.profession || "—" }}</span>
    </div>

    <div class="seat-tags">
      <span v-for="tag in tags" :key="tag" class="tag">{{ tag }}</span>
    </div>

    <div class="seat-meta">
      <span class="chip" :class="agent.alive ? 'chip-ok' : 'chip-out'">
        {{ agent.alive ? "存活" : "出局" }}
      </span>
      <span v-if="agent.camp_label" class="chip" :class="campClass">{{ agent.camp_label }}</span>
      <span v-for="(left, key) in agent.charges || {}" :key="key" class="chip">
        {{ key }} {{ left }}
      </span>
    </div>

    <div v-if="showNote && agent.notes && agent.notes.length" class="seat-note">
      {{ agent.notes[agent.notes.length - 1] }}
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  agent: { type: Object, required: true },
  no: { type: [String, Number], default: "" },
  // 牌面 id → URL（来自世界上下文；前端不拼路径）
  cardUrls: { type: Object, default: () => ({}) },
  // 独立使用时自带头与注记；嵌进插槽化座位时由外层槽接管（默认 true = 原有行为不变）
  showHead: { type: Boolean, default: true },
  showNote: { type: Boolean, default: true },
});

// 牌面图（后端给的 URL 优先；上下文里的映射表作为兜底）
const cardImage = computed(() => {
  const direct = str(props.agent.card_url || "");
  if (direct) return direct;
  const mapped = props.cardUrls[props.agent.card_image];
  return mapped ? str(mapped) : "";
});

function str(value) {
  return typeof value === "string" ? value.trim() : "";
}

// 人格标签：从 agent 档案的 personality 前缀切出来（通用层不认世界语义）
const tags = computed(() => {
  const text = String(props.agent.personality || "");
  const head = text.split("：")[0] || "";
  if (!head || head === text) return [];
  return head
    .split(/[、,]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 3);
});

// 阵营标签着色：按显示名里的关键词做通用判断（前端不认阵营语义，只区分语气）
const campClass = computed(() => {
  const text = String(props.agent.camp_label || "");
  if (/狼|恶|坏|敌/.test(text)) return "chip-wolf";
  if (/好|善|民|正/.test(text)) return "chip-village";
  return "";
});
</script>
