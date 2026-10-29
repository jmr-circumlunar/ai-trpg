<!--
  牌桌骨架（`layout: tabletop`）——**三段式**：左座位区 ｜ 中公共区 ｜ 右时间线。

  - 左区：每个角色一行（思考 | 角色卡），插槽清单见 `components/SeatColumn.vue`；
  - 中区：**公共区**（报幕 / 场景 / 公布 / 票型）——所有人当场都能看到的信息（S0-3）；
  - 右区：**一条连续时间线**（报幕作段首，段内商议/技能/公布/遗言/票型交错）。

  为什么中区单独劈出来（S0-3 · G9 痛点 5 / 9）：中区是「现在是什么」（读一眼就走），
  右区是「发生过什么」（要翻要查）。两类信息混在一列里，玩家既看不清当前，也翻不到历史。

  布局由世界包决定（`ui.layout`）——换布局不改页面代码（见 `lib/layouts.js`）。
  数据全部由 `IndexPage` 传进来（它才是取数的地方），本组件只管排版与交互状态。
-->
<template>
  <main class="layout tabletop">
    <SeatColumn
      :table="table"
      :thoughts="thoughts"
      :world-id="worldId"
      :card-urls="cardUrls"
      :expanded-out="expandedOut"
      @toggle-out="expandedOut = !expandedOut"
    />

    <CenterStage
      :timeline="timeline"
      :turn="turn"
      :choices="choices"
      :characters="characters"
      :live-text="liveText"
    />

    <GodTimeline :timeline="timeline" :turn="turn" />
  </main>
</template>

<script setup>
import { ref } from "vue";

import CenterStage from "../components/CenterStage.vue";
import GodTimeline from "../components/GodTimeline.vue";
import SeatColumn from "../components/SeatColumn.vue";

defineProps({
  table: { type: Object, default: () => ({ characters: [] }) },
  thoughts: { type: Array, default: () => [] },
  worldId: { type: String, default: "" },
  // 统一时间线（按发生顺序）
  timeline: { type: Array, default: () => [] },
  turn: { type: Number, default: 0 },
  turns: { type: Array, default: () => [] },
  liveEvents: { type: Array, default: () => [] },
  liveText: { type: String, default: "" },
  choices: { type: Array, default: () => [] },
  voteChoices: { type: Array, default: () => [] },
  picked: { type: Object, default: null },
  pendingVote: { type: Boolean, default: false },
  cardUrls: { type: Object, default: () => ({}) },
  characters: { type: Array, default: () => [] },
});

defineEmits(["pick", "pick-option"]);

// 出局席默认折叠；这里是**唯一**的展开状态源（座位列与折叠条共用）
const expandedOut = ref(false);
</script>
