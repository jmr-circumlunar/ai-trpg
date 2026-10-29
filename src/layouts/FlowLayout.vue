<!--
  通用跑团骨架（`layout: flow`）：左名单 + 中内容 + 右信息面板。

  这是**角色视角**（选了一个号位）时用的布局：身份卡、已确认的信息、决策面板、
  回合日志都在这里；上帝视角走 `TabletopLayout`。
-->
<template>
  <main class="layout">
    <PlayerSidebar
      :characters="characters"
      :world-info="worldInfo"
      @open-profile="$emit('open-profile', $event)"
    />

    <section class="time-column">
      <HumanPanel
        :ref="humanPanel"
        :panel="panel"
        :countdown="countdown"
        :submitting="submitting"
        :show-combat="showCombat"
        @submit="$emit('submit', $event)"
        @ghost="$emit('ghost', $event)"
      />

      <InfoPanels
        v-if="showIdentity"
        :characters="characters"
        :identity="identity"
        :speeches="speeches"
        :self-id="selfId"
      />

      <DecisionPanel
        :choices="pendingVote ? voteChoices : choices"
        :picked="picked"
        :title="pendingVote ? '轮到你投票' : '本回合的票型'"
        @pick="$emit('pick', $event)"
      />

      <TurnLog
        :turns="turns"
        :live-events="liveEvents"
        :live-text="liveText"
        @pick-option="$emit('pick-option', $event)"
      />
    </section>
  </main>
</template>

<script setup>
import DecisionPanel from "../components/DecisionPanel.vue";
import HumanPanel from "../components/HumanPanel.vue";
import InfoPanels from "../components/InfoPanels.vue";
import PlayerSidebar from "../components/PlayerSidebar.vue";
import TurnLog from "../components/TurnLog.vue";

defineProps({
  characters: { type: Array, default: () => [] },
  worldInfo: { type: Object, default: null },
  identity: { type: Object, default: null },
  speeches: { type: Array, default: () => [] },
  selfId: { type: String, default: "" },
  panel: { type: Object, default: null },
  countdown: { type: String, default: "" },
  submitting: { type: Boolean, default: false },
  showCombat: { type: Boolean, default: false },
  showIdentity: { type: Boolean, default: false },
  choices: { type: Array, default: () => [] },
  voteChoices: { type: Array, default: () => [] },
  picked: { type: Object, default: null },
  pendingVote: { type: Boolean, default: false },
  turns: { type: Array, default: () => [] },
  liveEvents: { type: Array, default: () => [] },
  liveText: { type: String, default: "" },
});

defineEmits(["open-profile", "pick", "pick-option", "submit", "ghost"]);
</script>
