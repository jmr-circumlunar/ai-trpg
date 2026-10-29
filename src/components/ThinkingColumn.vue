<!--
  思考气泡（**原子件**）：一名角色此刻心里在想什么。

  动态气泡的两种状态：
  - **浮现**：这人有新念头 → 气泡从角色卡那侧「浮」出来（放大 + 上浮 + 变实）；
  - **渐隐**：念头说完了、又没新的 → 气泡慢慢淡下去（保留一丝余韵，不硬消失）。

  动画是**纯 CSS**（`animation` 链：浮现 → 延时 → 渐隐），不占 CPU、不需要定时器；
  `prefers-reduced-motion` 下自动降级为直接显示。

  数据来自 SSE 的 `actor.thought`，后端已按 `visibility.thoughts` 逐人裁剪——
  角色视角下拿不到别人的心里话，这里不做任何推断。
-->
<template>
  <div
    class="think-cell"
    :class="{ 'think-out': !agent.alive, 'think-idle': !lines.length }"
    :data-think-for="agent.id || ''"
  >
    <div class="think-head">
      <span class="think-name">{{ agent.name }}</span>
      <span v-if="!agent.alive" class="think-flag think-out-flag">出局</span>
      <span v-else-if="!lines.length" class="think-flag think-idle-flag">静默</span>
      <span v-else class="think-flag think-count">{{ lines.length }}</span>
    </div>

    <ul v-if="lines.length" class="bubble-list">
      <li
        v-for="(line, index) in lines"
        :key="line.key"
        class="bubble"
        :class="{ 'bubble-latest': index === lines.length - 1 }"
      >
        <span class="bubble-text">{{ line.text }}</span>
      </li>
    </ul>

    <p v-else class="think-empty">（没有念头）</p>
  </div>
</template>

<script setup>
const props = defineProps({
  agent: { type: Object, required: true },
  // 这个角色最近的几条念头：`{key, text, ts}`（时间正序：最早的在前）
  lines: { type: Array, default: () => [] },
});
</script>
