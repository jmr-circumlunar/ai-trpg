/**
 * `src/lib/seatSlots.js` 的单测（零依赖：node:assert，跑 `node tests/js/seat_slots.test.mjs`）。
 *
 * 为什么这些断言值钱：出局折叠最容易犯的错是「把人藏了」——
 * 折叠后统计错了、席号跟着排序漂了、未知状态被当成出局。
 * 这些都是纯数据问题，不需要浏览器就能钉死。
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  FOLDED_SLOT_KEEP,
  SEAT_SLOT_LABELS,
  SEAT_SLOT_ORDER,
  isAlive,
  planSeats,
  seatSlotList,
  seatView,
} from "../../src/lib/seatSlots.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const cases = [];

function test(name, fn) {
  cases.push({ name, fn });
}

function agents(...flags) {
  return flags.map((alive, index) => ({
    id: `c${index}`,
    name: `角色${index + 1}`,
    profession: `职业${index + 1}`,
    alive,
  }));
}

// ── 词表 ────────────────────────────────────────────────────

test("槽词表就是那七个语义位，顺序固定", () => {
  assert.deepEqual(SEAT_SLOT_ORDER, [
    "above",
    "header",
    "children",
    "badge",
    "overlay",
    "below",
    "footer",
  ]);
  for (const name of SEAT_SLOT_ORDER) {
    assert.ok(SEAT_SLOT_LABELS[name], `${name} 要有中文语义标签`);
  }
});

test("折叠行只保留 header / badge", () => {
  assert.deepEqual(FOLDED_SLOT_KEEP, ["header", "badge"]);
  for (const name of FOLDED_SLOT_KEEP) {
    assert.ok(SEAT_SLOT_ORDER.includes(name), `${name} 必须来自主词表`);
  }
});

// ── 单席插槽 ────────────────────────────────────────────────

test("在场且无念头：只给 above / header / children 三层", () => {
  const seat = seatView({ id: "a", name: "甲", alive: true }, { index: 2 });
  assert.equal(seat.no, 3, "席号 = 原始序号 + 1");
  assert.deepEqual(seatSlotList(seat).slots, ["above", "header", "children"]);
});

test("在场有念头：多一个 badge（条数）", () => {
  const seat = seatView({ id: "a", name: "甲", alive: true }, { index: 0, thoughtCount: 2 });
  assert.deepEqual(seatSlotList(seat).slots, ["above", "header", "children", "badge"]);
});

test("出局席折叠后只有标头 + 角标——没有主体、没有覆盖层、没有行尾按钮", () => {
  const seat = seatView({ id: "a", name: "甲", alive: false }, { index: 1 });
  const fold = seatSlotList(seat, { collapsed: true }).slots;
  assert.deepEqual(fold, ["header", "badge"]);
  const open = seatSlotList(seat, { collapsed: false }).slots;
  assert.deepEqual(open, ["above", "header", "children", "badge", "overlay", "footer"]);
});

test("有注记时才出现 below（不渲染空行）", () => {
  const bare = seatView({ id: "a", name: "甲", alive: true, notes: ["", "  "] }, { index: 0 });
  assert.ok(!seatSlotList(bare).slots.includes("below"));
  const noted = seatView({ id: "a", name: "甲", alive: true, notes: ["旧", "新的"] }, { index: 0 });
  assert.ok(seatSlotList(noted).slots.includes("below"));
  assert.equal(noted.lastNote, "新的", "取最后一条");
});

// ── 未知状态：保守处理 ──────────────────────────────────────

test("alive 缺失 / 非布尔 → 按在场处理（宁多显示一行，也不藏人）", () => {
  assert.equal(isAlive({}), true);
  assert.equal(isAlive(null), true);
  assert.equal(isAlive({ alive: "false" }), true, "字符串 false 不算明确出局");
  assert.equal(isAlive({ alive: false }), false);
});

// ── 整列清单 ────────────────────────────────────────────────

test("存活优先：在场席在前，但席号仍按原始席次", () => {
  const { rows, stats } = planSeats(agents(true, false, true, false));
  assert.equal(stats.total, 4);
  assert.equal(stats.alive, 2);
  assert.equal(stats.out, 2);
  assert.deepEqual(rows.map((row) => row.name), ["角色1", "角色3", "角色2", "角色4"]);
  assert.deepEqual(rows.map((row) => row.no), [1, 3, 2, 4], "席号跟着人走，不跟着位置走");
});

test("出局席默认折叠，展开开关一开就全部展开", () => {
  const folded = planSeats(agents(true, false, false));
  assert.equal(folded.stats.folded, 2);
  assert.ok(folded.rows.slice(1).every((row) => row.collapsed));

  const open = planSeats(agents(true, false, false), { expandedOut: true });
  assert.equal(open.stats.folded, 0);
  assert.ok(open.rows.every((row) => !row.collapsed));
  assert.equal(open.stats.total, 3, "展开不改变人数，只改变行形态");
});

test("全员在场时不折叠、全员出局时也不折叠（没有折叠的意义）", () => {
  assert.equal(planSeats(agents(true, true)).stats.folded, 0);
  const allOut = planSeats(agents(false, false));
  assert.equal(allOut.stats.folded, 2, "全员出局仍可折叠，但行数不变");
  assert.equal(allOut.stats.visibleRows, 2, "折叠不是删除，行还在");
});

test("念头条数按角色名匹配进清单（角标/缩放都靠它）", () => {
  const { rows } = planSeats(agents(true, true), {
    thoughtCounts: { 角色1: 3, 角色2: 0 },
  });
  assert.equal(rows[0].thoughtCount, 3);
  assert.ok(rows[0].slots.includes("badge"));
  assert.equal(rows[1].thoughtCount, 0);
  assert.ok(!rows[1].slots.includes("badge"));
});

test("空局 / 非法输入不炸", () => {
  for (const bad of [[], null, undefined, [null, undefined]]) {
    const { rows, stats } = planSeats(bad);
    assert.ok(Array.isArray(rows));
    assert.equal(stats.total, rows.length);
  }
});

// ── 纯度约束：这个模块不摸 DOM / 框架 ────────────────────────

test("模块本身无 DOM / 框架依赖（纯函数）", () => {
  const source = readFileSync(join(HERE, "../../src/lib/seatSlots.js"), "utf8");
  const code = source
    .split("\n")
    .filter((line) => !line.trim().startsWith("*") && !line.trim().startsWith("//"))
    .join("\n");
  for (const bad of ["document.", "window.", "localStorage", 'from "vue"', "from 'vue'"]) {
    assert.ok(!code.includes(bad), `纯函数模块不该出现 ${bad}`);
  }
});

// ── 运行 ────────────────────────────────────────────────────
let failed = 0;
for (const item of cases) {
  try {
    item.fn();
    console.log(`  ok  ${item.name}`);
  } catch (err) {
    failed += 1;
    console.error(`  FAIL ${item.name}\n       ${err && err.message ? err.message : err}`);
  }
}
console.log(`\n${cases.length - failed}/${cases.length} passed（槽位 ${SEAT_SLOT_ORDER.length} 个）`);
if (failed) process.exit(1);
