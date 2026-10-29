<!--
  统一时间线（上帝视角右半）：**一条按发生顺序的流**。

  段首 = 主持人报幕（金色分隔线）；段内按到达顺序交错渲染：
  场景叙事 / 组内商议 / 技能发动 / 行动与心理活动 / 公布 / 遗言。

  数据来源：`IndexPage` 按 SSE 到达顺序累积的 `timeline`（到达顺序 = 实际发生顺序）。
  以前是「上面一堆阶段报幕、下面一堆角色行动」两段分离，看不出谁在什么时候做了什么。
-->
<template>
  <section class="god-timeline">
    <div class="tl-head">
      <span class="tl-title">时间线</span>
      <span class="tl-sub">第 {{ turn }} 回合 · {{ items.length }} 条</span>
    </div>

    <div class="tl-body">
      <template v-for="(item, index) in items" :key="index">
        <!-- 段首：主持人报幕 -->
        <div v-if="item.kind === 'banner'" class="tl-banner">
          <span class="tl-mark"></span>{{ item.text }}
        </div>

        <!-- 场景叙事：GM 写的这一段 -->
        <div v-else-if="item.kind === 'scene'" class="tl-scene">{{ item.text }}</div>

        <!-- 其余事件 -->
        <div v-else class="tl-item" :class="'tl-' + item.kind">
          <span class="tl-who">{{ item.who }}</span>
          <div class="tl-main">
            <span class="tl-text">{{ item.text }}</span>
            <span v-if="item.extra" class="tl-extra">{{ item.extra }}</span>
            <div v-if="item.thought" class="tl-thought">{{ item.thought }}</div>
          </div>
        </div>
      </template>

      <div v-if="!items.length" class="tl-empty">
        这一回合还没有发生任何事——点右上角「推进回合」。
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  timeline: { type: Array, default: () => [] },
  turn: { type: Number, default: 0 },
});

// 时间线本身就是有序的：不再按类别分组重排（那会破坏发生顺序）
const items = computed(() => props.timeline || []);
</script>
