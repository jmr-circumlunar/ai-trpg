<!--
  玩家页。

  **契约**：本页有 15 个元素 id 与后端/测试绑定（`tests/test_web_ui.py::INDEX_IDS`），
  改 id 前先看那个测试。

  能力开关（combat / equipment / human_seat …）用 `v-if` 直接不渲染对应区块，
  不再靠 `blocks.js` 事后隐藏——隐藏的元素仍会被读屏软件念出来。
-->
<template>
  <!-- 对局页是「一屏看完」的牌桌：整页固定不滚动，块内各自滚（方案 38） -->
  <div class="shell play">
    <header class="topbar">
      <h1>{{ worldName || "对局" }}</h1>
      <span class="sub" id="hdr-note">{{ note }}</span>
      <span class="sub" id="hdr-sub">{{ sub }}</span>
      <span class="grow"></span>

      <!-- 操作区集中在右上：视角 / 推进 / 存档名 / 保存 / 退出 -->
      <label class="sub label" v-if="characters.length">视角
        <select id="viewer" v-model="viewerId" class="field" style="width: auto">
          <option value="">上帝视角（看全部）</option>
          <option v-for="char in characters" :key="char.id" :value="char.id">
            {{ char.name }}
          </option>
        </select>
      </label>
      <!-- 思考档位：同时改思考深度 / 输出预算 / 推理步数（档位定义在 providers.yaml） -->
      <label class="sub label">思考
        <select id="effort" v-model="effort" class="field" style="width: auto" @change="applyEffort">
          <option value="standard">标准</option>
          <option value="deep">深入</option>
          <option value="max">最大</option>
        </select>
      </label>
      <button
        id="advance"
        class="btn btn-sm"
        type="button"
        :disabled="busy || !sid"
        @click="advance"
      >
        推进一回合
      </button>
      <input
        id="save-name"
        v-model="saveName"
        class="field"
        placeholder="存档名"
        style="width: 120px"
      />
      <button class="btn btn-ghost btn-sm" type="button" @click="saveGame">保存</button>
      <button class="btn btn-ghost btn-sm" type="button" @click="leaveGame">退出</button>
      <a class="btn btn-ghost btn-sm" href="/">大厅</a>
      <!-- 复盘必须带 sid：`/trace` 是静态页，自己从 query 读 sid（G9 bug 2） -->
      <a id="trace-link" class="btn btn-ghost btn-sm" :href="traceHref">复盘</a>
    </header>

    <div id="statusbar" class="topbar statusbar">
      <span class="chip" v-if="state?.region_label">{{ state.region_label }}</span>
      <span class="chip" v-if="state?.clock_label">{{ state.clock_label }}</span>
      <span class="chip" v-if="state?.stage !== undefined">幕次 {{ state.stage }}</span>
      <span class="chip" v-if="state?.tension !== undefined">张力 {{ state.tension }}</span>
      <span class="chip" v-if="turns.length">第 {{ turns.length }} 回合</span>
      <span class="grow"></span>
      <!-- 生成中的可见标志：流结束（正常 / 出错 / 断线）后必须消失（G9 bug 1） -->
      <span class="chip" id="streaming" v-if="streaming">正在生成…</span>
      <span class="sub" id="timing">{{ timing }}</span>
      <button
        id="hmode"
        class="btn btn-ghost btn-sm"
        type="button"
        style="display: none"
        v-if="caps.has('human_seat')"
        @click="toggleHumanMode"
      >
        {{ humanMode === "wait" ? "托管模式" : "等待模式" }}
      </button>
    </div>

    <div id="progress-wrap" class="progress-wrap">
      <div id="progress" class="progress"><i id="pbar-i"></i></div>
      <span id="pbar-label"></span>
    </div>

    <!-- 人类席位（悬浮交互，与骨架无关） -->
    <HumanPanel
      ref="humanPanel"
      :panel="panel"
      :countdown="countdown"
      :submitting="submitting"
      :show-combat="caps.has('combat')"
      @submit="submitHuman(false)"
      @ghost="submitHuman(true)"
    />

    <!-- 骨架由**世界包**决定（`ui.layout`）：上帝视角用世界声明的骨架；
         角色视角一律走玩家视图（不显示别人的思考）。 -->
    <component
      :is="layoutComponent"
      v-bind="layoutProps"
      @pick="onPick"
      @pick-option="pickOption"
      @open-profile="openProfile"
    />

    <!-- 发牌动效（公共组件）：统一牌背 → 依次翻开露出身份牌面 -->
    <DealCards
      v-if="dealing"
      :cards="dealCards"
      :back-url="backUrl"
      @done="dealing = false"
    />

    <ProfileDrawer :open="drawerOpen" :profile="profile" @close="drawerOpen = false" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import DealCards from "../components/DealCards.vue";
import HumanPanel from "../components/HumanPanel.vue";
import ProfileDrawer from "../components/ProfileDrawer.vue";
import FlowLayout from "../layouts/FlowLayout.vue";
import { friendlyError, getJson, postJson, sendJson, streamTurn } from "../lib/api.js";
import { resolveLayout } from "../lib/layouts.js";
import { tracePath } from "../lib/route.js";
import { loadWorldTheme } from "../lib/theme.js";
import { cardIdOf, cardUrl, loadContext } from "../lib/world-context.js";

const sid = ref("");
const worlds = ref([]);
const world = ref("ironvale");
// 人数默认 8：狼人杀是固定 8 人板（3 狼 + 3 神 + 2 民），
// 调到 8 之后 agent 自动入场、开局才抽角色。其它世界仍可选。
const players = ref(8);
const provider = ref("mock");
const batch = ref(3);
const locked = ref(false);
const note = ref("");
const sub = ref("");

const state = ref(null);
const turns = ref([]);
const liveText = ref("");
// 本回合的实时动态（商议 / 遗言 / 介入）：`done` 时整体归档进回合记录，然后清空。
// 后端已按查看者裁过，前端「收到就渲染」，不按 viewer 再判一次（C21）。
const liveEvents = ref([]);
// **统一时间线**：按 SSE 到达顺序累积（= 实际发生顺序）。
// 以前是「上面一堆阶段报幕、下面一堆角色行动」——两段分离，看不出谁在什么时候做了什么。
const timeline = ref([]);
// 当前时间线所属回合（换回合就清空）
const timelineTurn = ref(0);
// 单回合实时事件上限：长局（多轮商议 × 多组成员 × 连跑）会把数组撑大，
// 截断保留最近 N 条即可——玩家看的是「刚才发生了什么」，不是全量审计。
const LIVE_LIMIT = 200;
const timing = ref("");
const busy = ref(false);
// 流式回合是否在跑。**唯一真源**：SSE 的 done / error / 断线都必须把它置回 false，
// 否则界面永久停在「叙事生成中…」，玩家以为卡死（G9 bug 1）。
const streaming = ref(false);
// 判定档位 id → 中文 label（S1-3）：后端给的映射，前端只查表不翻译
const outcomeLabels = ref({});

const panel = ref(null);
// 人类席位的待办（行动 or 投票）：靠轮询拿到
const pending = ref(null);
let pollTimer = null;
const countdown = ref("");
const submitting = ref(false);
const humanMode = ref("wait");
const caps = ref(new Set());

const drawerOpen = ref(false);
const profile = ref(null);

const humanPanel = ref(null);

let countdownTimer = null;
let progressTimer = null;

// 世界名：快照里只有 world.id（`snapshot()` 按 C56 不带冗余字段），
// 所以回落到世界列表里查名字——否则进局后标题是空的，看着像「换了个游戏」。
const worldName = computed(() => {
  const live = state.value?.world || {};
  if (live.name) return live.name;
  const hit = worlds.value.find((item) => item.id === live.id);
  return hit ? hit.name : "";
});

// 复盘入口：地址构造只留一处（`lib/route.js`），页面不自己拼 query
const traceHref = computed(() => tracePath(sid.value));

// 身份卡：**只渲染后端明确给出的本人信息**。前端不从别人的数据里推断身份——
// 那是后端可见性矩阵的职责（C56）。后端还没给本人身份时，这里就是空的。
const identity = ref(null);
// 视角：选一个角色 = 以他的身份看这局（空 = 上帝视角）。
// 身份卡的内容由后端按可见性矩阵给，前端不推断。
const viewerId = ref("");

// 上帝视角的数据（角色视角不请求）：牌桌一次拿全，播报与心声靠 SSE 累积。
const table = ref({ turn: 0, winner: "", characters: [] });
// **世界上下文**：渲染所需的全部世界侧数据（布局 / 主题 / 牌面 / 人数 / agent）。
// 唯一取数入口是 `lib/world-context.js`（带缓存与去重），页面不自己 fetch。
const context = ref(null);
const banners = ref([]);
const thoughts = ref([]);
// 单回合心声上限：8 人 × 多轮对话会很长，保留最近 N 条即可
const THOUGHT_LIMIT = 60;

const godMode = computed(() => !viewerId.value);

// 骨架由**世界包**决定（`ui.layout`）：上帝视角用世界声明的骨架；
// 角色视角一律玩家视图（不显示别人的思考，也不显示全部牌桌）。
const layoutComponent = computed(() =>
  godMode.value ? resolveLayout(context.value) : FlowLayout
);

// 传给骨架的数据（骨架只管排版，取数全在这里）
const layoutProps = computed(() => ({
  table: table.value,
  thoughts: thoughts.value,
  worldId: worldId.value,
  timeline: timeline.value,
  // 段标题用「时间线实际到第几回合」：刷新后 `turns` 是空的，但回填的历史已经到第 N 回合
  turn: timelineTurn.value || turns.value.length,
  turns: turns.value,
  liveEvents: liveEvents.value,
  liveText: liveText.value,
  choices: choices.value,
  voteChoices: voteChoices.value,
  picked: picked.value,
  pendingVote: pendingVote.value,
  characters: characters.value,
  worldInfo: worldInfo.value,
  identity: identity.value,
  speeches: speeches.value,
  selfId: viewerId.value,
  panel: panel.value,
  countdown: countdown.value,
  submitting: submitting.value,
  showCombat: caps.value.has("combat"),
  showIdentity: caps.value.has("identity"),
  // 牌面 id → URL（世界上下文给的资产清单）
  cardUrls: Object.fromEntries(
    ((context.value && context.value.ui && context.value.ui.cards) || []).map((item) => [
      item.id,
      item.url,
    ])
  ),
}));

// 世界在进局时**固定**：这局的世界由 session 决定，不再由页面上的下拉决定。
// 以前 `world` 初值是 ironvale 且进局后没人覆盖它 → 标题与主题都显示成铁谷。
const worldId = computed(() => state.value?.world?.id || world.value);
watch(
  worldId,
  (id) => {
    if (id) world.value = id;
  },
  { immediate: true }
);

// 存档名：**落库**（`PATCH /api/sessions/{sid}`），不再存浏览器本地
// ——本地存换设备就没了，而且大厅/存档页/对局页会各存一份、彼此对不上。
const saveName = ref("");
// 思考档位：standard / deep / max（定义在 `config/providers.yaml: efforts`）
const effort = ref("deep");

/** 切换思考档位：影响玩家 Agent 的思考深度、输出预算与推理步数。 */
async function applyEffort() {
  if (!sid.value) return;
  try {
    const res = await sendJson(
      `/api/sessions/${encodeURIComponent(sid.value)}/effort`,
      { effort: effort.value },
      "POST"
    );
    const applied = res?.applied || {};
    note.value = `思考档位：${effort.value}${
      applied.steps ? `（推理 ${applied.steps} 轮 · 深度 ${applied.reasoning_effort}）` : ""
    }`;
  } catch (err) {
    note.value = friendlyError(err);
  }
}

async function saveGame() {
  if (!sid.value) return;
  const name =
    String(saveName.value || "").slice(0, 24) || `第 ${turns.value.length} 回合`;
  try {
    await sendJson(`/api/sessions/${encodeURIComponent(sid.value)}`, { name }, "PATCH");
    saveName.value = name;
    note.value = `已保存：${name}`;
  } catch (err) {
    note.value = friendlyError(err);
  }
}

function leaveGame() {
  location.href = worldId.value ? `/world/${encodeURIComponent(worldId.value)}` : "/";
}

// ── 发牌动效（公共组件 DealCards 负责翻牌动画）─────────────────
const dealing = ref(false);
// 牌面数据：身份图 URL **来自世界上下文**（前端不拼路径、不认世界语义）
const dealCards = computed(() =>
  (table.value.characters || []).map((agent) => ({
    key: agent.id,
    name: agent.name,
    role: agent.profession || "",
    image: cardUrl(context.value, cardIdOf(context.value, agent.profession)),
  }))
);
const backUrl = computed(() => cardUrl(context.value, "back"));

async function loadWorldContext() {
  const id = state.value?.world?.id || world.value;
  if (!id) return;
  try {
    context.value = await loadContext(id);
    // 主题地址由上下文给（前端不再自己拼 `/theme.css`）
    if (context.value?.ui?.theme_url) loadWorldTheme(context.value.ui.theme_url);
  } catch (err) {
    note.value = friendlyError(err);
  }
}

async function refreshTable() {
  if (!sid.value) {
    table.value = { turn: 0, winner: "", characters: [] };
    return;
  }
  try {
    const query = viewerId.value
      ? `?viewer=${encodeURIComponent(viewerId.value)}`
      : "";
    table.value = await getJson(`/api/sessions/${sid.value}/table${query}`);
  } catch {
    // 拿不到就保留旧值：牌桌不该阻塞推进
  }
}

/**
 * 进站回填：本局最近 N 回合的事件从**服务端只读**取回（G9 bug 3）。
 *
 * 以前时间线只累积「本次挂载之后收到的 SSE 事件」，刷新一页就归零（角色状态还在、
 * 发生过的事全没了）。回填的窗口大小与裁剪规则都在服务端（`/history` +
 * `ruleset.yaml: ui.history_turns`），前端只负责渲染——不在这里拼第二份真相。
 */
async function loadHistory() {
  if (!sid.value) return 0;
  try {
    const query = viewerId.value
      ? `?viewer=${encodeURIComponent(viewerId.value)}`
      : "";
    const data = await getJson(`/api/sessions/${sid.value}/history${query}`);
    const items = Array.isArray(data?.timeline) ? data.timeline : [];
    if (!items.length) return 0;
    timeline.value = items;
    timelineTurn.value = Number(items[items.length - 1].turn || 0);
    return items.length;
  } catch {
    // 回填失败不阻塞对局：时间线空着，玩家仍能推进
    return 0;
  }
}

// 狼人杀是固定 8 人板：切到这个世界就把人数按住（agent 自动入场）
watch(world, (id) => {
  if (id === "werewolf") players.value = 8;
});

watch(viewerId, async (cid) => {
  identity.value = null;
  if (!cid || !sid.value) return;
  try {
    identity.value = await getJson(
      `/api/sessions/${sid.value}/identity/${encodeURIComponent(cid)}` +
        `?viewer=${encodeURIComponent(cid)}`
    );
  } catch {
    identity.value = null;
  }
});

// 决策面板：后端给出可选项与票数时才非空
const choices = ref([]);
const picked = ref("");

// 发言流：公开叙事按时间顺序（只取最近 20 条）
const speeches = computed(() => {
  const out = [];
  for (const turn of turns.value) {
    for (const actor of turn.actors || []) {
      if (actor.narration) {
        out.push({ actor: actor.id, name: actor.name, text: actor.narration });
      }
    }
  }
  return out.slice(-20);
});

function onPick(choiceId) {
  if (pendingVote.value) {
    submitVote(choiceId);
    return;
  }
  picked.value = choiceId;
}

// ── 人类投票 ──────────────────────────────────────────────
const pendingVote = computed(
  () => Boolean(pending.value && pending.value.kind === "vote")
);

const voteChoices = computed(() => {
  if (!pendingVote.value) return [];
  return (pending.value.candidates || []).map((cid) => ({
    id: cid,
    label: characters.value.find((c) => c.id === cid)?.name || cid,
  }));
});

async function pollPending() {
  if (!sid.value) return;
  try {
    const data = await getJson(`/api/sessions/${sid.value}/pending`);
    pending.value = data.pending || null;
  } catch {
    // 轮询失败不打断对局：下一轮再问
  }
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollPending, 1000);
}

async function submitVote(choiceId) {
  if (!pending.value) return;
  try {
    await postJson(`/api/sessions/${sid.value}/action`, {
      char_id: pending.value.char_id,
      action: "",
      target: choiceId,
      intent: "normal",
      ghost: false,
    });
    pending.value = null;
  } catch (err) {
    timing.value = friendlyError(err);
  }
}
const characters = computed(() => state.value?.characters || []);
const worldInfo = computed(() => state.value || null);

onMounted(async () => {
  await loadWorlds();
  await bootFromQuery();
});

onUnmounted(() => {
  if (countdownTimer) clearInterval(countdownTimer);
  if (progressTimer) clearInterval(progressTimer);
  if (pollTimer) clearInterval(pollTimer);
});

async function loadWorlds() {
  try {
    const data = await getJson("/api/worlds");
    worlds.value = data.worlds || [];
    const params = new URLSearchParams(location.search);
    if (params.get("world")) world.value = params.get("world");
    if (params.get("players")) players.value = Number(params.get("players"));
  } catch (err) {
    note.value = friendlyError(err);
  }
}

async function bootFromQuery() {
  const want = new URLSearchParams(location.search).get("sid");
  if (!want) {
    // 对局页必须带 `sid`：没有存档就没有局可打，回大厅选世界（方案 36）
    location.href = "/";
    return;
  }
  try {
    const data = await postJson(
      `/api/sessions/${encodeURIComponent(want)}/resume?provider=${provider.value}`
    );
    sid.value = data.session_id || want;
    locked.value = true;
    applySnapshot(data);
    await loadCapabilities();
    // 世界上下文先来：牌桌的牌面 URL 依赖它
    await loadWorldContext();
    await refreshTable();
    // 刷新后先把本局已经发生过的事补回来（否则时间线只剩刷新之后的）
    await loadHistory();
    // 从房间进来（?deal=1）就播发牌动效：统一牌背 → 翻开身份
    if (new URLSearchParams(location.search).get("deal") === "1") dealing.value = true;
    startPolling();
  } catch (err) {
    note.value = friendlyError(err);
  }
}

async function newSession() {
  busy.value = true;
  try {
    const data = await postJson("/api/sessions", {
      world: world.value,
      players: players.value,
      provider: provider.value,
    });
    sid.value = data.session_id;
    locked.value = true;
    note.value = "已开局";
    await loadCapabilities();
    // 牌桌先拉一次：开局就能看到「谁是谁、谁是狼」，不用等第一回合跑完
    await refreshTable();
    startPolling();
    await advance();
  } catch (err) {
    note.value = friendlyError(err);
  } finally {
    busy.value = false;
  }
}

async function loadCapabilities() {
  if (!sid.value) return;
  try {
    const data = await getJson(`/api/sessions/${sid.value}/config`);
    caps.value = new Set(data.systems || []);
  } catch {
    // 拿不到能力清单就按全开处理：宁可多显示，不要让页面像坏了
    caps.value = new Set();
  }
}

function applySnapshot(snapshot) {
  state.value = snapshot || null;
  if (snapshot?.human_mode) humanMode.value = snapshot.human_mode;
  // 判定档位的中文 label（S1-3）：后端给 id→label 映射，前端不再显示内部枚举
  if (snapshot?.outcome_labels) outcomeLabels.value = snapshot.outcome_labels;
  // 世界主题（方案 35）：对局页也按世界换皮；没有主题的世界回落到基础暗色
  if (snapshot?.world?.id) loadWorldTheme(snapshot.world.id);
}

/**
 * 把后端的判定结果串翻译成中文（`success (18 vs 10)` → `成功 (18 vs 10)`）。
 * 查不到映射就原样显示（不猜、不吞），映射来自后端的档位表。
 */
function outcomeText(raw) {
  const text = String(raw || "");
  if (!text) return "";
  const id = text.split(" ")[0];
  const label = outcomeLabels.value[id];
  return label ? label + text.slice(id.length) : text;
}

// ── 推进 ────────────────────────────────────────────────
async function advance() {
  if (!sid.value) {
    note.value = "先点「开局」";
    return;
  }
  busy.value = true;
  try {
    for (let i = 0; i < Number(batch.value || 1); i += 1) {
      if (state.value?.status === "ended") break;
      await advanceOnce();
    }
  } catch (err) {
    // 出错也要落在可见位置：之前异常直接冒泡到点击处理，页面上什么提示都没有
    note.value = friendlyError(err);
  } finally {
    busy.value = false;
    streaming.value = false;
  }
}

async function advanceOnce() {
  const query = viewerId.value
    ? `?viewer=${encodeURIComponent(viewerId.value)}`
    : "";
  // 先清空：实时区只反映**本回合**正在发生的事
  liveEvents.value = [];
  banners.value = [];
  thoughts.value = [];
  streaming.value = true;
  let result = null;
  let failure = "";
  try {
    result = await streamTurn(
      `/api/sessions/${sid.value}/advance/stream${query}`,
      onSseEvent
    );
  } catch (err) {
    failure = friendlyError(err);
    throw err;
  } finally {
    // G9 bug 1：正常 done、服务端 error 事件、网络断线三种收场都必须复位
    // 「生成中」标志——以前 `narrate` 之后再没人清它，界面永久显示「叙事生成中…」。
    streaming.value = false;
    timing.value = failure ? `本回合中断：${failure}` : "本回合结束";
  }
  if (!result) return;
  // 判定档位 label（S1-3）：done 回执里也带一份，兜住「中途才拿到映射」的情况
  if (result.outcome_labels) outcomeLabels.value = result.outcome_labels;
  liveText.value = "";
  // 牌桌刷新：身份不会变，但存活、资源、已确认信息每回合都会变
  await refreshTable();
  // 票型 → 决策面板的可选项（弃权不列）
  const counts = (result.vote_result && result.vote_result.counts) || {};
  choices.value = Object.entries(counts)
    .filter(([id]) => id !== "__abstain__")
    .map(([id, votes]) => ({ id, label: id, votes }))
    .sort((a, b) => b.votes - a.votes);
  // 归档：把实时累积的动态一起带进回合记录，回合结束后仍然看得到
  turns.value = [...turns.value, normalizeTurn(result, liveEvents.value)];
  liveEvents.value = [];
  if (result.state) applySnapshot(result.state);
  if (result.ending) {
    turns.value[turns.value.length - 1].ending = result.ending.summary || "本局结束";
  }
}

// 角色 id → 名字：只查本局角色表；查不到就显示 id 原文（不猜、不隐藏）
function nameOf(id) {
  return characters.value.find((char) => char.id === id)?.name || id;
}

function pushLive(kind, data) {
  liveEvents.value.push({ kind, ...data });
  if (liveEvents.value.length > LIVE_LIMIT) {
    liveEvents.value.splice(0, liveEvents.value.length - LIVE_LIMIT);
  }
}

function normalizeTurn(res, events) {
  const list = events || [];
  // 遗言以 `done` 回执的 `deaths[].last_words`（160 字）为准；
  // SSE 的 `eulogy` 只有 80 字，仅在死亡条目没带上遗言时兜底——同一句话不列两遍。
  const said = {};
  for (const item of list) {
    if (item.kind === "eulogy" && item.id) said[item.id] = item.text;
  }
  const deaths = (res.deaths || []).map((entry) => {
    // 出局条目的两种形态：`{id, cause}`（apply 直接淘汰）与
    // `{char_id, turn, kind}`（濒死结算），两种都要认，漏一种就少显示一个出局者
    const id = entry.id || entry.char_id || "";
    return {
      id,
      name: id ? nameOf(id) : "—",
      cause: entry.cause || entry.kind || "",
      lastWords: entry.last_words || said[id] || "",
    };
  });
  return {
    turn: res.turn,
    scene: res.scene || "",
    options: res.options || [],
    actors: res.actors || [],
    rejected: res.rejected || [],
    changes: [],
    // 组内商议：后端已按视角裁过，收到即该看见
    events: list.filter((item) => item.kind === "council"),
    // 技能发动：私密技能只发给本人（后端裁过），公开技能（开枪/自爆）所有人可见
    skills: list.filter((item) => item.kind === "skill"),
    // 介入名单不在 `done` 回执里（C21 未扩字段），只能靠实时事件归档
    intervention: list
      .filter((item) => item.kind === "intervention")
      .flatMap((item) => item.names || []),
    deaths,
  };
}

/** 往统一时间线追加一条（顺序 = 到达顺序 = 实际发生顺序）。 */
function pushTimeline(kind, payload) {
  timeline.value.push({
    kind,
    turn: timelineTurn.value || 0,
    who: "",
    text: "",
    extra: "",
    thought: "",
    ...payload,
  });
  if (timeline.value.length > 300) {
    timeline.value.splice(0, timeline.value.length - 300);
  }
}

function onSseEvent(event, data) {
  switch (event) {
    case "stage":
      timing.value = data.phase === "director" ? "导演决策…" : "GM 写场景…";
      break;
    // 主持人播报：时间线的**段首**（换回合就在这里断开）
    case "banner": {
      const turn = data.turn || 0;
      // 换回合**不再清空**时间线：有了进站回填，清空等于把刚补回来的历史又抹掉。
      // 段落靠报幕行区分（带 turn 号），总量由下面的 300 条上限兜住。
      timelineTurn.value = turn || timelineTurn.value;
      pushTimeline("banner", { turn, text: data.text || "" });
      timing.value = data.text || timing.value;
      break;
    }
    case "npc":
      timing.value = `NPC 动向：${(data.names || []).join("、") || "—"}`;
      break;
    case "actor":
      timing.value = `${data.name} 的判定：${outcomeText(data.outcome)}`;
      // 心理活动：上帝视角/本人 才有这个字段（后端按 visibility.thoughts 裁过）
      if (data.thought) {
        // 带时间戳：思考气泡据此做「浮现 → 渐隐」
        thoughts.value.push({
          name: data.name || "",
          thought: data.thought,
          ts: Date.now(),
        });
        if (thoughts.value.length > THOUGHT_LIMIT) {
          thoughts.value.splice(0, thoughts.value.length - THOUGHT_LIMIT);
        }
      }
      pushTimeline("actor", {
        who: data.name || "",
        text: data.action || outcomeText(data.outcome) || "",
        extra: outcomeText(data.outcome) || "",
        thought: data.thought || "",
      });
      break;
    case "narrate":
      streaming.value = true;
      timing.value = "叙事生成中…";
      break;
    case "commit":
      timing.value = `状态已提交（v${data.version}）`;
      break;
    case "scene":
      liveText.value = data.text || "";
      if (data.text) pushTimeline("scene", { text: data.text });
      break;
    // 组内商议：只有本组成员（与观察者）会收到，前端拿到就显示
    case "council":
      pushLive("council", {
        group: data.group || "",
        name: data.name || "",
        text: data.text || "",
      });
      pushTimeline("council", {
        who: `${data.group || "小组"}·${data.name || ""}`,
        text: data.text || "",
      });
      timing.value = `${data.group || "小组"}商议中：${data.name || ""}`;
      break;
    // 出局遗言：公开信息，出局那一刻就播
    case "eulogy":
      pushLive("eulogy", {
        id: data.id || "",
        name: data.name || "",
        text: data.text || "",
      });
      pushTimeline("eulogy", { who: data.name || "", text: data.text || "" });
      timing.value = `${data.name || "有人"} 留下遗言`;
      break;
    // 介入：代码强制到场的角色（不进 `done` 回执，靠实时归档）
    case "intervention":
      pushLive("intervention", { names: data.names || [] });
      timing.value = `介入：${(data.names || []).join("、") || "—"}`;
      break;
    // 技能发动：私密技能只有本人（与观察者）收得到，后端已裁过
    case "skill":
      pushLive("skill", {
        id: data.id || "",
        name: data.name || "",
        label: data.label || data.skill || "",
        targetName: data.target_name || "",
        effect: data.effect || "",
        result: data.result || "",
      });
      pushTimeline("skill", {
        who: `${data.name || ""} · ${data.label || data.skill || ""}`,
        text: data.result || "",
        extra: data.target_name || "",
      });
      timing.value = `${data.name || ""} 发动「${data.label || data.skill || ""}」`;
      break;
    case "broadcast": {
      // 代码侧公布（出局名单 / 平安夜）
      const names = data.names || [];
      timing.value = names.length ? `公布：${names.join("、")} 出局` : "公布：本轮无人出局";
      pushTimeline("broadcast", {
        who: "公布",
        text: names.length ? `${names.join("、")} 出局` : "本轮无人出局",
      });
      break;
    }
    case "human_wait":
      showHumanPanel(data);
      break;
    case "human_done":
      hideHumanPanel();
      break;
    default:
      break;
  }
}

// ── 人类席位 ────────────────────────────────────────────
function showHumanPanel(data) {
  panel.value = {
    who: `${data.name} · 第 ${data.turn} 回合轮到你`,
    state: "",
    briefs: data.briefs && data.briefs.length ? `此前：${data.briefs.join("；")}` : "此前：—",
    enemies: data.enemies || [],
    skills: data.skills || [],
    // S4-3：当前可见信息 + 带理由的建议（建议不代替人决策）
    visible: data.visible || {},
    suggestions: data.suggestions || [],
  };
  startCountdown(data.timeout_ms);
}

function hideHumanPanel() {
  panel.value = null;
  countdown.value = "";
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

function startCountdown(ms) {
  if (countdownTimer) clearInterval(countdownTimer);
  const total = Math.max(0, Number(ms || 0));
  const until = Date.now() + total;
  const tick = () => {
    const left = Math.max(0, Math.round((until - Date.now()) / 1000));
    countdown.value = `剩余 ${left}s（超时由 AI 代打）`;
    if (left <= 0 && countdownTimer) {
      clearInterval(countdownTimer);
      countdownTimer = null;
    }
  };
  tick();
  countdownTimer = setInterval(tick, 1000);
}

async function submitHuman(ghost) {
  if (!panel.value || submitting.value) return;
  const payload = humanPanel.value?.getPayload?.() || {};
  if (!ghost && !String(payload.action || "").trim()) {
    countdown.value = "先写一句要做什么";
    return;
  }
  submitting.value = true;
  try {
    await postJson(`/api/sessions/${sid.value}/action`, {
      char_id: panel.value.char_id || null,
      action: payload.action || "",
      thought: payload.thought || "",
      skill: payload.skill || "",
      intent: payload.intent || "normal",
      mode: payload.mode || "",
      target: payload.target || "",
      ghost: Boolean(ghost),
    });
    hideHumanPanel();
  } catch (err) {
    countdown.value = friendlyError(err);
  } finally {
    submitting.value = false;
  }
}

async function toggleHumanMode() {
  humanMode.value = humanMode.value === "wait" ? "handoff" : "wait";
  try {
    await postJson(`/api/sessions/${sid.value}/human/mode`, { mode: humanMode.value });
  } catch (err) {
    note.value = friendlyError(err);
  }
}

function pickOption(opt) {
  if (humanPanel.value?.setAction) humanPanel.value.setAction(opt.label || "");
  else timing.value = opt.label || "";
}

// ── 档案抽屉 ────────────────────────────────────────────
async function openProfile(cid) {
  drawerOpen.value = true;
  profile.value = null;
  try {
    profile.value = await getJson(
      `/api/sessions/${sid.value}/characters/${encodeURIComponent(cid)}/profile`
    );
  } catch (err) {
    profile.value = null;
    note.value = friendlyError(err);
  }
}
</script>

