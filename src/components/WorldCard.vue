<!--
  世界卡片（**公共组件**）：大厅用；以后别处要列世界也直接用它。

  只渲染世界摘要，不做任何取数——数据由父级给（大厅拿列表，详情页拿上下文）。
-->
<template>
  <article
    class="world-card"
    role="button"
    tabindex="0"
    @click="$emit('open', world)"
    @keydown.enter="$emit('open', world)"
  >
    <div class="wc-top">
      <span class="wc-name">{{ world.name }}</span>
      <span v-if="world.open" class="chip chip-ok">进行中</span>
    </div>

    <p class="wc-tone">{{ world.tone || "还没有写简介" }}</p>

    <div class="wc-foot">
      <span v-if="world.players && world.players.default" class="chip">
        {{ world.players.default }} 人
      </span>
      <span class="grow"></span>
      <span class="sub">{{ lastPlayed }}</span>
    </div>
  </article>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  world: { type: Object, required: true },
});

defineEmits(["open"]);

const lastPlayed = computed(() => {
  const stamp = String(props.world.last_played || "");
  if (!stamp) return "还没玩过";
  return `上次 ${stamp.slice(0, 16).replace("T", " ")}`;
});
</script>
