<!--
  房间（开局设置）：号位 × agent。

  - 号位是**位置**，agent 是「人」；换人 = 换谁坐这个位（方案 35 口径）。
  - 人数由世界声明（`world.players`）：没有人数下拉，只有「加号位 / 减号位」，
    受 min/max 限制；狼人杀 min=max=8，两个按钮都是灰的。
  - 身份不在这里选：开局才抽（发牌动效），抽到什么由世界牌堆决定。
  - 每个号位右下角留「换成本人」入口（本期灰显，下一期实现真人席位）。
-->
<template>
  <div class="shell">
    <header class="topbar">
      <a class="btn btn-ghost btn-sm" :href="`/world/${worldId}`">← 返回</a>
      <h1>{{ world.name || "开局设置" }}</h1>
      <span class="sub">{{ seats.length }} 个号位 · 身份开局抽取</span>
      <span class="grow"></span>
      <button class="btn" type="button" :disabled="busy" @click="start">
        {{ busy ? "发牌中…" : "开始对局" }}
      </button>
    </header>

    <main class="lobby">
      <div class="lobby-head">
        <div>
          <div class="lobby-title">号位与坐庄的人</div>
          <div class="sub">
            同一个 agent 可以出现在不同世界、不同存档；这里换人只影响本局。
          </div>
        </div>
        <div class="seat-tools">
          <button class="btn btn-ghost btn-sm" type="button" :disabled="!canAdd" @click="addSeat">
            ＋ 加号位
          </button>
          <button class="btn btn-ghost btn-sm" type="button" :disabled="!canRemove" @click="removeSeat">
            − 减号位
          </button>
        </div>
      </div>

      <div v-if="note" class="note">{{ note }}</div>

      <div class="seat-list">
        <article v-for="(seat, index) in seats" :key="seat.key" class="seat-row">
          <span class="seat-no" :title="seat.label">{{ index + 1 }}</span>

          <select
            class="field agent-pick"
            :value="seat.agent_id"
            @change="pick(seat, $event.target.value)"
          >
            <option v-for="agent in agents" :key="agent.id" :value="agent.id">
              {{ agent.name }} · {{ shortTrait(agent) }}
            </option>
          </select>

          <span class="sub seat-trait">{{ traitOf(seat.agent_id) }}</span>

          <!-- S4-3：号位可声明「这一席我来坐」（可点，不再灰显） -->
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            :class="{ 'btn-on': seat.human }"
            :title="seat.human ? '这一席由真人坐（点击改为模型）' : '这一席由模型坐（点击换成本人）'"
            @click="toggleHuman(seat)"
          >
            {{ seat.human ? "真人席" : "换成本人" }}
          </button>
        </article>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";

import { friendlyError, getJson, postJson } from "../lib/api.js";
import { go, query } from "../lib/route.js";
import { loadContext } from "../lib/world-context.js";
import { loadWorldTheme } from "../lib/theme.js";

const worldId = query("world") || "werewolf";
const world = ref({});
const context = ref(null);
const agents = ref([]);
const seats = ref([]);
const busy = ref(false);
const note = ref("");

const pool = computed(() => world.value.players || {});
const canAdd = computed(() => {
  const max = Number(pool.value.max || 8);
  return seats.value.length < max;
});
const canRemove = computed(() => {
  const min = Number(pool.value.min || 2);
  return seats.value.length > min;
});

onMounted(async () => {
  try {
    // 世界上下文：人数区间、名字、主题都由它给（页面不自己拉 /api/worlds 拼字段）
    const ctx = await loadContext(worldId);
    context.value = ctx;
    world.value = { name: ctx?.world?.name || "", players: ctx?.players || {} };
    if (ctx?.ui?.theme_url) loadWorldTheme(ctx.ui.theme_url);
    // `/api/agents` = 该 owner 的 agent 档案（建局时 `agent_ids` 只能指向它）；
    // 首次访问后端会用 config/agents.yaml 补齐「出厂的人」
    agents.value = (await getJson("/api/agents")).agents || [];
    const want = Number(pool.value.default || 8);
    for (let index = 0; index < want; index += 1) addSeat(true);
  } catch (err) {
    note.value = friendlyError(err);
  }
});

function addSeat(silent = false) {
  if (!canAdd.value && !silent) return;
  const used = new Set(seats.value.map((seat) => seat.agent_id));
  const next = agents.value.find((agent) => !used.has(agent.id)) || agents.value[0];
  const index = seats.value.length + 1;
  // S4-3：席位声明（世界包优先、规则层兜底）给出这一席的默认 label / 是否真人
  const declared = seatDecl(index);
  seats.value.push({
    key: `seat-${Date.now()}-${seats.value.length}`,
    agent_id: next ? next.id : "",
    label: declared.label || `${index} 号位`,
    human: Boolean(declared.human),
  });
}

/** 席位声明（世界上下文若给出 `ui.seats` 就用它，否则退回「N 号位」）。 */
function seatDecl(index) {
  const list = (context.value && context.value.ui && context.value.ui.seats) || [];
  return list.find((item) => Number(item && item.index) === index) || {};
}

/** 换成本人 / 改回模型（一个席位一个人）。 */
function toggleHuman(seat) {
  seat.human = !seat.human;
}

function removeSeat() {
  if (!canRemove.value) return;
  seats.value.pop();
}

// 换人：**一个 agent 只能坐一个位**（后端也会拒）。选到别人时两人对调，
// 比起弹错更符合「换座位」的直觉。
function pick(seat, agentId) {
  const other = seats.value.find((item) => item !== seat && item.agent_id === agentId);
  if (other) other.agent_id = seat.agent_id;
  seat.agent_id = agentId;
}

function agentName(id) {
  const agent = agents.value.find((item) => item.id === id);
  return agent ? agent.name : id;
}

function traitOf(id) {
  const agent = agents.value.find((item) => item.id === id);
  return agent ? String(agent.persona || "") : "";
}

function shortTrait(agent) {
  const traits = agent.traits;
  if (Array.isArray(traits)) return traits.slice(0, 2).join("·");
  return String(traits || "");
}

async function start() {
  if (busy.value) return;
  busy.value = true;
  note.value = "";
  try {
    const created = await postJson("/api/sessions", {
      world: worldId,
      players: seats.value.length,
      provider: "auto",
      agent_ids: seats.value.map((seat) => seat.agent_id),
      // S4-3：真人席位用**号位序号**表达（房间页还不知道角色 id）
      human_seats: seats.value
        .map((seat, index) => (seat.human ? index + 1 : 0))
        .filter((index) => index > 0),
    });
    // 发牌动效在对局页播（那里才有身份数据可翻牌）；`deal=1` 触发
    go(`/play?sid=${encodeURIComponent(created.session_id)}&deal=1`);
  } catch (err) {
    note.value = friendlyError(err);
    busy.value = false;
  }
}
</script>



