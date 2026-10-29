<!--
  信息对抗类世界的三块通用组件：**玩家列表 / 身份卡 / 发言流**。

  这三块不含任何世界专属概念：谁在场上（roster）、我是谁（identity）、
  大家都说了什么（speech）。任何需要「信息不对称」的世界都能直接用。

  **安全边界**：身份卡只渲染后端明确给出的**本人**信息。
  前端不推断、不从别人的数据里推导身份——那是后端可见性矩阵的职责（C56）。
-->
<template>
  <div class="panel" style="margin-bottom: 14px">
    <h2>{{ title }}</h2>

    <!-- 玩家列表 -->
    <div class="roster">
      <div
        v-for="char in characters"
        :key="char.id"
        class="roster-item"
        :class="{ 'is-self': char.id === selfId, 'is-dead': char.position === 'dead' }"
      >
        <span class="roster-dot" :class="{ 'is-dead': char.position === 'dead' }"></span>
        <span class="who">{{ char.name }}</span>
        <span class="role">{{ char.profession || "—" }}</span>
        <span class="spacer"></span>
        <span v-if="char.position === 'dead'" class="badge failure">出局</span>
      </div>
      <div v-if="!characters.length" class="empty">还没有人入场</div>
    </div>

    <!-- 身份卡（只显示后端给的本人信息） -->
    <div v-if="identity" class="identity-card" style="margin-top: 12px">
      <div class="role">{{ identity.profession || identity.role || "身份不明" }}</div>
      <div class="camp">{{ identity.camp_label || "阵营未知" }}</div>
      <div class="note">{{ identity.note || "" }}</div>
      <div class="private-only">这张卡只有你看得见。</div>
    </div>
    <div v-else class="muted" style="margin-top: 10px">
      身份卡：<span class="muted">本局没有提供本人身份</span>
    </div>

    <!-- 私有信息与技能资源：只显示「本人」的。
         后端只对本人与观察者下发 notes，这里不推断、不聚合别人的。 -->
    <div v-if="me && ((me.notes && me.notes.length) || hasCharges)" class="identity-card" style="margin-top: 12px">
      <div class="role">你已确认的信息</div>
      <div v-for="(line, index) in me.notes || []" :key="'note' + index" class="note">
        {{ line }}
      </div>
      <div v-if="hasCharges" class="camp">可用资源：{{ chargeText }}</div>
      <div v-if="!me.notes || !me.notes.length" class="muted">还没有确认过任何信息</div>
      <div class="private-only">这些内容只有你看得见。</div>
    </div>

    <!-- 发言流 -->
    <div v-if="speeches.length" class="speech-stream" style="margin-top: 12px">
      <div
        v-for="(line, index) in speeches"
        :key="index"
        class="speech"
        :class="{ 'is-self': line.actor === selfId }"
      >
        <div class="who">{{ line.name }}</div>
        <div class="text">{{ line.text }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  title: { type: String, default: "场上的人" },
  characters: { type: Array, default: () => [] },
  identity: { type: Object, default: null },
  speeches: { type: Array, default: () => [] },
  selfId: { type: String, default: "" },
});

// 本人在这局里的条目：`notes` / `skill_charges` 都挂在这里（后端只对本人下发 notes）。
// 上帝视角（selfId 为空）不显示这一块——那是「你的私有信息」，不是全局面板。
const me = computed(() =>
  props.selfId ? props.characters.find((char) => char.id === props.selfId) || null : null
);

const charges = computed(() => (me.value && me.value.skill_charges) || {});
const hasCharges = computed(() => Object.keys(charges.value).length > 0);
const chargeText = computed(() =>
  Object.entries(charges.value)
    .map(([key, left]) => `${key} ${left}`)
    .join(" · ")
);
</script>
