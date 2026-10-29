<!--
  中心公共区（S0-3）：**所有人当场都能看到的公开信息**集中在这里。

  为什么单独一块（G9 痛点 5 / 9）：报幕、公布、票型原本散在右侧时间线里，
  和「谁在什么时候做了什么」的历史流水混在一起，公共信息没有自己的位置，
  也没有层次——玩家得靠翻流水才知道现在是什么阶段、谁票最多。

  职责边界（与 `GodTimeline` 分工，不重复、不抢活）：
  - 本组件只显示**当前**的公开状态：最新报幕 / 当前场景 / 最新公布 / 票型；
  - 时间线仍然是**全过程**流水（含历史报幕、历史票型）——那是「发生过什么」，
    这里是「现在是什么」。删掉历史会退回到方案 38 之前「看不出先后」的老问题。
  - 数据全部由骨架从 `IndexPage` 传入：本组件不取数、不推断、不认世界语义。
-->
<template>
  <section class="center-stage">
    <div class="cs-head">
      <span class="cs-title">公共区</span>
      <span class="cs-sub" v-if="turn">第 {{ turn }} 回合</span>
    </div>

    <div class="cs-body">
      <!-- 报幕：当前回合主持人说的话（最醒目的一层） -->
      <article v-if="banner" class="cs-block cs-banner">
        <span class="cs-label">报幕</span>
        <p class="cs-text">{{ banner.text }}</p>
      </article>

      <!-- 场景：当前正在发生的公开叙事 -->
      <article v-if="scene" class="cs-block cs-scene">
        <span class="cs-label">场景</span>
        <p class="cs-text cs-text-dim">{{ scene }}</p>
      </article>

      <!-- 公布：代码侧公布的出局名单 / 平安夜 -->
      <article v-if="broadcast" class="cs-block cs-broadcast">
        <span class="cs-label">公布</span>
        <p class="cs-text">{{ broadcast.text }}</p>
      </article>

      <!-- 票型：每个候选人的票数（条长按最高票归一） -->
      <article v-if="voteRows.length" class="cs-block cs-votes">
        <span class="cs-label">票型</span>
        <ul class="cs-vote-list">
          <li v-for="row in voteRows" :key="row.id" class="cs-vote">
            <span class="cs-vote-name">{{ row.name }}</span>
            <span class="cs-vote-bar"><i :style="{ width: row.pct }"></i></span>
            <span class="cs-vote-num">{{ row.votes }}</span>
          </li>
        </ul>
      </article>

      <p v-if="!hasAny" class="cs-empty">
        这一回合还没有公开信息——推进一回合后，报幕 / 公布 / 票型会出现在这里。
      </p>
    </div>
  </section>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  // 有序时间线（到达顺序 = 发生顺序）：公共区只从里面取**最新**一条同类信息
  timeline: { type: Array, default: () => [] },
  turn: { type: Number, default: 0 },
  // 票型：`{ id, label, votes }`（后端给的票数统计）
  choices: { type: Array, default: () => [] },
  // id → 名字（票型里显示名字而不是内部 id，G9 痛点 6）
  characters: { type: Array, default: () => [] },
  // 单回合实时场景文本（SSE `scene` 事件）
  liveText: { type: String, default: "" },
});

/** 时间线里最后一条指定类型。 */
function lastOf(kind) {
  const items = props.timeline || [];
  for (let i = items.length - 1; i >= 0; i -= 1) {
    if (items[i] && items[i].kind === kind) return items[i];
  }
  return null;
}

const banner = computed(() => lastOf("banner"));
const broadcast = computed(() => lastOf("broadcast"));
const scene = computed(() => props.liveText || (lastOf("scene") || {}).text || "");

const nameOf = computed(() => {
  const map = {};
  for (const item of props.characters || []) {
    if (item && item.id) map[String(item.id)] = item.name || String(item.id);
  }
  return map;
});

const voteRows = computed(() => {
  const list = (props.choices || []).filter((item) => item && Number(item.votes) > 0);
  const max = list.reduce((best, item) => Math.max(best, Number(item.votes) || 0), 0);
  return list.map((item) => {
    const votes = Number(item.votes) || 0;
    return {
      id: String(item.id),
      name: nameOf.value[String(item.id)] || item.label || String(item.id),
      votes,
      // 最高票占满条：数字之外再给一眼可比的形状
      pct: `${max > 0 ? Math.round((votes / max) * 100) : 0}%`,
    };
  });
});

const hasAny = computed(
  () => Boolean(banner.value || broadcast.value || scene.value || voteRows.value.length)
);
</script>
