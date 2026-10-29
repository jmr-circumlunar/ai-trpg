<!--
  决策面板：把「你要在几个选项里挑一个」这件事做成明确的选择，而不是让玩家打字。

  数据来自后端的集体决策结果（C57）：`choices[]` 带票数，`picked` 是当前选择。
  组件本身不认识任何具体世界——投票、拍卖、抉择都长这样。
-->
<template>
  <div class="decision" style="margin-top: 12px">
    <h3>{{ title }}</h3>
    <p v-if="!choices.length" class="muted" style="margin: 0">
      本回合没有需要你决策的事项。
    </p>
    <div v-else class="choices">
      <button
        v-for="choice in choices"
        :key="choice.id"
        class="choice"
        :class="{ 'is-picked': choice.id === picked }"
        type="button"
        @click="$emit('pick', choice.id)"
      >
        <span>{{ choice.label }}</span>
        <span v-if="choice.votes !== undefined" class="tally">{{ choice.votes }} 票</span>
      </button>
    </div>
    <div v-if="picked" class="muted" style="margin-top: 8px">已选：{{ pickedLabel }}</div>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  title: { type: String, default: "轮到你决定" },
  choices: { type: Array, default: () => [] },
  picked: { type: String, default: "" },
});

defineEmits(["pick"]);

const pickedLabel = computed(
  () => props.choices.find((c) => c.id === props.picked)?.label || ""
);
</script>
