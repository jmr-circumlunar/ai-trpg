<!--
  回合日志：场景 + 结构化选项 + 每个角色的行动与判定 + 本回合动态（组内商议 /
  强制介入）+ 本回合出局（含遗言）+ 变化与被拒的 delta。

  **可见性由后端决定**：送来的 `council` 事件已经按查看者裁过，这里不做二次判断；
  这里只负责「有就显示」。打字机效果保留（点一下跳过）——它是「正在生成」的唯一
  视觉信号。
-->
<template>
  <div id="log">
    <div v-if="!turns.length && !liveText && !liveEvents.length" class="empty">
      还没有开始。点右上角「开局」生成一局，或选好世界后按「推进一回合」。
    </div>

    <div v-for="(turn, index) in turns" :key="turn.turn" class="turn">
      <div class="hdr">第 {{ turn.turn }} 回合</div>
      <div class="scene">{{ turn.scene }}</div>

      <div v-if="turn.options && turn.options.length" class="opts">
        <button
          v-for="opt in turn.options"
          :key="opt.id || opt.label"
          class="opt"
          type="button"
          @click="$emit('pick-option', opt)"
        >
          {{ opt.label }}
          <span v-if="opt.hint" class="hint">{{ opt.hint }}</span>
        </button>
      </div>

      <div v-for="actor in turn.actors || []" :key="actor.id" class="act">
        <div class="who">
          {{ actor.name }}
          <span class="badge" :class="tierOf(actor.outcome)">{{ actor.outcome }}</span>
        </div>
        <details v-if="actor.thought" class="think">
          <summary>盘算</summary>
          <p>{{ actor.thought }}</p>
        </details>
        <p class="do">{{ actor.action }}</p>
        <p v-if="actor.narration">{{ actor.narration }}</p>
      </div>

      <!-- 本回合动态：组内商议、技能发动、强制介入 -->
      <div
        v-if="
          (turn.events && turn.events.length) ||
          (turn.skills && turn.skills.length) ||
          (turn.intervention && turn.intervention.length)
        "
        class="beats"
      >
        <div class="chdr">本回合动态</div>
        <div v-for="(beat, bi) in turn.events || []" :key="'c' + bi" class="beat beat-council">
          <span class="who">{{ beat.group }}·{{ beat.name }}</span>{{ beat.text }}
        </div>
        <div v-for="(cast, si) in turn.skills || []" :key="'s' + si" class="beat beat-skill">
          <span class="who">{{ cast.name }} · {{ cast.label }}</span>
          <template v-if="cast.targetName">→ {{ cast.targetName }}</template>
          <span v-if="cast.result" class="muted">（{{ cast.result }}）</span>
        </div>
        <div v-if="turn.intervention && turn.intervention.length" class="beat beat-join">
          <span class="who">介入</span>{{ turn.intervention.join("、") }}
        </div>
      </div>

      <!-- 本回合出局：谁出局、什么原因、留下什么话（遗言以回执里的完整文本为准） -->
      <div v-if="turn.deaths && turn.deaths.length" class="changes" style="border-color: #4d2a27">
        <div class="chdr" style="color: var(--danger)">本回合出局</div>
        <div v-for="(dead, di) in turn.deaths" :key="'d' + di">
          {{ dead.name }}<span v-if="dead.cause" class="muted">（{{ dead.cause }}）</span>
          <div v-if="dead.lastWords" class="beat-eulogy">遗言：「{{ dead.lastWords }}」</div>
        </div>
      </div>

      <div v-if="turn.changes && turn.changes.length" class="changes">
        <div class="chdr">本回合变化</div>
        <div v-for="(change, ci) in turn.changes" :key="ci">{{ change }}</div>
      </div>

      <div v-if="turn.rejected && turn.rejected.length" class="changes" style="border-color: #4d2a27">
        <div class="chdr" style="color: var(--danger)">被拒绝的改动</div>
        <div v-for="(item, ri) in turn.rejected" :key="ri">{{ item }}</div>
      </div>

      <div v-if="turn.ending" class="changes">
        <div class="chdr">本局结束</div>
        <div>{{ turn.ending }}</div>
      </div>
    </div>

    <!-- 回合内的实时动态：与「正在生成」并列，回合结束后归档进上面的回合区块 -->
    <div v-if="liveEvents.length" class="turn live">
      <div class="hdr">本回合动态</div>
      <div class="beats live">
        <div
          v-for="(beat, bi) in liveEvents"
          :key="'lb' + bi"
          class="beat"
          :class="'beat-' + beat.kind"
        >
          <template v-if="beat.kind === 'council'">
            <span class="who">{{ beat.group }}·{{ beat.name }}</span>{{ beat.text }}
          </template>
          <template v-else-if="beat.kind === 'eulogy'">
            <span class="who">{{ beat.name }} 遗言</span>「{{ beat.text }}」
          </template>
          <template v-else-if="beat.kind === 'skill'">
            <span class="who">{{ beat.name }} · {{ beat.label }}</span>
            <template v-if="beat.targetName">→ {{ beat.targetName }}</template>
            <span v-if="beat.result" class="muted">（{{ beat.result }}）</span>
          </template>
          <template v-else>
            <span class="who">介入</span>{{ (beat.names || []).join("、") }}
          </template>
        </div>
      </div>
    </div>

    <div v-if="liveText" class="turn live">
      <div class="hdr">正在生成</div>
      <div class="scene" @click="skipTyping">{{ liveText }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from "vue";

import { outcomeTier } from "../lib/api.js";

const props = defineProps({
  turns: { type: Array, default: () => [] },
  // 本回合正在发生的动态（商议 / 遗言 / 介入）；`done` 后由父组件归档进回合记录
  liveEvents: { type: Array, default: () => [] },
  liveText: { type: String, default: "" },
});

const emit = defineEmits(["pick-option"]);

const shown = ref("");
let timer = null;

function skipTyping() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  shown.value = props.liveText;
}

watch(
  () => props.liveText,
  (text) => {
    if (timer) clearInterval(timer);
    timer = null;
    if (!text) {
      shown.value = "";
      return;
    }
    shown.value = "";
    let i = 0;
    timer = setInterval(() => {
      shown.value = text.slice(0, i += 2);
      if (i >= text.length && timer) {
        clearInterval(timer);
        timer = null;
      }
    }, 30);
  }
);

function tierOf(outcome) {
  return outcomeTier(outcome);
}
</script>
