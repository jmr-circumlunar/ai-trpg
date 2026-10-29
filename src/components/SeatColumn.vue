<!--
  牌桌 · 角色区（上帝视角左半）：**一行一角色**，行内左边是思考气泡、右边是角色卡。

  ## 插槽清单（S0-3）
  座位不再是「两个固定块」，而是一张**插槽清单**：`above / header / children / badge /
  overlay / below / footer` 七个语义槽（词表见 `lib/seatSlots.js`）。模板按清单 `v-for`
  渲染，哪一层显示由 `seatSlotList()` 决定——加一层（例如「卡下注记」）不用改结构，
  只在清单里加一个槽；外部也能用同名 `v-slot` 覆盖任意一层（默认内容保留为 fallback）。

  ## 归属引导（S0-3 · G9 痛点 4）
  「同行对齐」只保证 y 相同，**横向配对仍靠人眼**。所以：
  - `above` 槽画一条**贯通连接线**：从思考列一侧的圆点拉到卡列，行首即标明这对是同一人；
  - 气泡尖角加长到越过列间距、对准卡片左上角（`.bubble::after` + `.seat-body` 的 gap）；
  - 每个角色卡与思考块都带 `data-think-for` / `data-seat`，用于截图核验与自动化断言。

  ## 出局折叠（S0-3 · G9 痛点 2 / 5）
  出局席默认折叠成**一条细行**（标头 + 角标 + 展开按钮），在场席全部展开且排在前面。
  8 席里 6 席出局时，左列不再有 3/4 屏是「（没有念头）」的死区；折叠条可一键展开回看。

  ## 座位自动缩放（S0-2）
  布局数学在 `lib/tableLayout.js`（纯函数、可单测），本组件只做两件事——量出可用高度、
  把算出的缩放写成 CSS 变量 `--seat-scale`。缩放**只缩盒子不缩字号**，不是 CSS transform。
-->
<template>
  <aside ref="columnRef" class="seat-column" :style="{ '--seat-scale': scale }">
    <div class="seat-col-head">
      <span class="seat-col-title">座位 · 思考</span>
      <span class="seat-col-sub">{{ stats.alive }} 在场 / {{ stats.total }} 席</span>
      <!-- 缩放后仍放不下时如实说明：玩家知道「可以滚」，而不是以为漏了人 -->
      <span v-if="needsScroll" class="seat-col-sub seat-col-hint">席位较多，可滚动查看</span>
    </div>

    <article
      v-for="row in seats.rows"
      :key="row.key"
      class="seat-row"
      :class="{
        'seat-row-out': !row.alive,
        'seat-row-folded': row.collapsed,
      }"
      :data-seat="row.no"
    >
      <!-- 按插槽清单逐层渲染：顺序 = SEAT_SLOT_ORDER，显隐由 seatSlotList 决定 -->
      <template v-for="name in row.slots" :key="name">
        <!-- above：归属连接层（圆点 + 贯通线），把思考列与卡列连成一行 -->
        <div v-if="name === 'above'" class="seat-above">
          <slot name="above" :row="row">
            <span class="seat-above-dot"></span>
            <span class="seat-above-line"></span>
          </slot>
        </div>

        <!-- header：行标头（席号 / 名字 / 身份 / 状态），卡身不再重复一份 -->
        <header v-else-if="name === 'header'" class="seat-header">
          <slot name="header" :row="row">
            <span class="seat-no">{{ row.no }}</span>
            <span class="seat-name">{{ row.name }}</span>
            <span class="seat-role">{{ row.profession || "—" }}</span>
            <span class="seat-state" :class="row.alive ? 'chip-ok' : 'chip-out'">
              {{ row.alive ? "在场" : "出局" }}
            </span>
          </slot>
        </header>

        <!-- children：主体（思考气泡 | 角色卡），逐行同高对齐 -->
        <div v-else-if="name === 'children'" class="seat-body">
          <slot name="children" :row="row">
            <ThinkingColumn :agent="agentOf(row)" :lines="thoughtsOf(agentOf(row))" />
            <SeatCards
              :agent="agentOf(row)"
              :no="row.no"
              :card-urls="cardUrls"
              :show-head="false"
              :show-note="false"
            />
          </slot>
        </div>

        <!-- badge：角标（出局 / 念头条数） -->
        <span v-else-if="name === 'badge'" class="seat-badge">
          <slot name="badge" :row="row">
            <span v-if="!row.alive" class="seat-badge-out">出局</span>
            <span v-else-if="row.thoughtCount" class="seat-badge-count">
              {{ row.thoughtCount }} 念头
            </span>
          </slot>
        </span>

        <!-- overlay：出局覆盖层（降饱和，不用虚线边框 → G9 痛点 13） -->
        <span v-else-if="name === 'overlay'" class="seat-overlay">
          <slot name="overlay" :row="row"></slot>
        </span>

        <!-- below：卡下注记（最近一条已确认信息） -->
        <p v-else-if="name === 'below'" class="seat-below">
          <slot name="below" :row="row">{{ row.lastNote }}</slot>
        </p>

        <!-- footer：行尾操作（出局席展开 / 收起） -->
        <div v-else-if="name === 'footer'" class="seat-footer">
          <slot name="footer" :row="row">
            <button
              class="btn btn-ghost btn-sm seat-fold-btn"
              type="button"
              @click="$emit('toggle-out')"
            >
              {{ expandedOut ? "收起出局席" : "展开出局席" }}
            </button>
          </slot>
        </div>
      </template>
    </article>

    <p v-if="!seats.rows.length" class="seat-empty">这一局还没有座位。</p>

    <!-- 折叠总开关：一眼看出「还有几个人在场下」，不用滚动找 -->
    <button
      v-if="stats.out"
      class="seat-fold-toggle"
      type="button"
      @click="$emit('toggle-out')"
    >
      {{ expandedOut ? `收起 ${stats.out} 个出局席 ▲` : `出局 ${stats.out} 席已折叠 ▼` }}
    </button>
  </aside>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { planSeats } from "../lib/seatSlots.js";
import { columnLayout } from "../lib/tableLayout.js";
import SeatCards from "./SeatCards.vue";
import ThinkingColumn from "./ThinkingColumn.vue";

const props = defineProps({
  table: { type: Object, default: () => ({ characters: [] }) },
  thoughts: { type: Array, default: () => [] },
  worldId: { type: String, default: "" },
  // 牌面 id → URL（来自世界上下文；前端不拼路径）
  cardUrls: { type: Object, default: () => ({}) },
  // 出局席是否展开：默认收起（G9 痛点 2 / 5）
  expandedOut: { type: Boolean, default: false },
});

defineEmits(["toggle-out"]);

// 每人最多展示这么多条念头（气泡多了会挤；更早的会自己淡掉）
const MAX_LINES = 3;

// 基准行高与行距：与 `theme.css` 的 `.seat-row` 实测高度 / margin 对齐。
// 这是**视觉基准**，缩放由 `columnLayout` 按可用高度算，不在这里手调。
const ROW_HEIGHT = 150;
const ROW_GAP = 9;
const HEAD_GAP = 10;
// 折叠行高 / 展开行高：折叠行只剩标头 + 角标 + 行尾，约三成高
const FOLD_RATIO = 0.34;

const columnRef = ref(null);
const scale = ref(1);
const needsScroll = ref(false);
let observer = null;

// 每个角色最近几条念头（顺序：最早的在前）
const thoughtMap = computed(() => {
  const map = new Map();
  for (const item of props.thoughts || []) {
    const key = String(item.name || "");
    if (!key) continue;
    const list = map.get(key) || [];
    list.push({
      key: `${key}-${item.ts || list.length}`,
      text: item.thought,
      ts: item.ts || 0,
    });
    map.set(key, list);
  }
  return map;
});

// 插槽清单：存活优先 + 出局折叠。模板只按返回的行渲染，不自己判断存活。
const seats = computed(() =>
  planSeats((props.table && props.table.characters) || [], {
    expandedOut: props.expandedOut,
    thoughtCounts: Object.fromEntries(
      [...thoughtMap.value.entries()].map(([name, list]) => [name, list.length])
    ),
  })
);
const stats = computed(() => seats.value.stats);

/** 行 → 原始角色对象（SeatCards / ThinkingColumn 要完整字段，不只结构视图）。 */
function agentOf(row) {
  const list = (props.table && props.table.characters) || [];
  return list.find((item) => String(item.id) === row.id) || { id: row.id, name: row.name };
}

/** 这个角色最近的几条念头（按名字匹配：SSE 的 actor 事件带 name）。 */
function thoughtsOf(agent) {
  const list = thoughtMap.value.get(String((agent && agent.name) || "")) || [];
  return list.slice(-MAX_LINES);
}

/** 量可用高度 → 算缩放。纯计算全在 `columnLayout`，这里只喂数字。 */
function measure() {
  const el = columnRef.value;
  const host = el && el.parentElement;
  if (!host) return;
  const style = getComputedStyle(host);
  const padY =
    (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
  const head = el.querySelector(".seat-col-head");
  const headH = head ? head.getBoundingClientRect().height + HEAD_GAP : 0;
  const available = Math.max(0, host.clientHeight - padY - headH);

  // 折叠行只有展开行的一小半高：按「折叠行 × 折叠高 + 展开行 × 行高」折成**等效行高**，
  // 否则出局席一多就算出「放不下」的假结论（S0-3 实测：6 席折叠仍被判要滚动）。
  const rows = seats.value.rows || [];
  const folded = rows.filter((row) => row.collapsed).length;
  const open = rows.length - folded;
  const foldHeight = Math.round(ROW_HEIGHT * FOLD_RATIO);
  const total = open * ROW_HEIGHT + folded * foldHeight;
  const effectiveRow = rows.length ? Math.round(total / rows.length) : ROW_HEIGHT;

  const layout = columnLayout({
    count: rows.length,
    availableHeight: available,
    rowHeight: effectiveRow,
    gap: ROW_GAP,
  });
  // 同值不写：ResizeObserver 里改自身尺寸会再触发一次观察，同值短路即不会自激
  if (layout.scale !== scale.value) scale.value = layout.scale;
  // 「是否还要滚」以**实测**为准：模型只算名义盒高，卡里的文字会把行撑得更高
  needsScroll.value = layout.needsScroll;
  nextTick(() => {
    const node = columnRef.value;
    if (node) needsScroll.value = node.scrollHeight > node.clientHeight + 1;
  });
}

onMounted(() => {
  measure();
  // 量的是**父容器**（网格区）而不是本列：本列高度由内容决定，量它永远是"刚好够"
  const host = columnRef.value && columnRef.value.parentElement;
  if (host && typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(() => measure());
    observer.observe(host);
  }
  window.addEventListener("resize", measure);
});

onBeforeUnmount(() => {
  if (observer) observer.disconnect();
  observer = null;
  window.removeEventListener("resize", measure);
});

// 席位数变化（换局 / 中途加入）或折叠切换要重新算
watch(
  () => [stats.value.visibleRows, props.expandedOut],
  () => measure()
);
</script>
