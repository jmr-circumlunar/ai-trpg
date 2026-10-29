/**
 * `src/lib/tableLayout.js` 的单测（零依赖：只用 node:assert，跑 `node tests/js/table_layout.test.mjs`）。
 *
 * 为什么不用 jest/vitest：仓库没有前端测试运行时，也不想为一个纯函数模块引入
 * 一套测试框架（S0-2 的约束是「不引入新框架」）。纯函数用 `node:assert` 足够。
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  SCALE_FLOOR,
  SCALE_STEP,
  SEAT_GAP,
  clamp,
  columnLayout,
  columnRects,
  fitUniformScale,
  minSeparation,
  quantizeDown,
  ringLayout,
  ringPoint,
  ringRadii,
  ringRects,
  shrinkRects,
  withinBounds,
} from "../../src/lib/tableLayout.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const cases = [];

function test(name, fn) {
  cases.push({ name, fn });
}

// ── 基础几何 ────────────────────────────────────────────────
test("ringPoint：4 席从 12 点起等分", () => {
  const p0 = ringPoint(0, 4, { cx: 0, cy: 0, rx: 100, ry: 50, startAngleDeg: -90 });
  const p1 = ringPoint(1, 4, { cx: 0, cy: 0, rx: 100, ry: 50, startAngleDeg: -90 });
  assert.ok(Math.abs(p0.x - 0) < 1e-9, "第 0 席应在正上方（x=0）");
  assert.ok(Math.abs(p0.y - -50) < 1e-9, "第 0 席 y 应为 -ry");
  assert.ok(Math.abs(p1.x - 100) < 1e-9, "第 1 席应在正右方（x=rx）");
  assert.ok(Math.abs(p1.y - 0) < 1e-9, "第 1 席 y 应为 0");
});

test("minSeparation：分离、相切、重叠三种情形", () => {
  const a = { x: 0, y: 0, w: 10, h: 10 };
  assert.equal(minSeparation([a, { x: 20, y: 0, w: 10, h: 10 }]), 10);
  assert.equal(minSeparation([a, { x: 10, y: 0, w: 10, h: 10 }]), 0);
  assert.equal(minSeparation([a, { x: 4, y: 4, w: 10, h: 10 }]), -6);
  assert.equal(minSeparation([a]), Infinity, "单席永不重叠");
});

test("shrinkRects：只缩盒子（中心不动），间距反而变大", () => {
  const rects = [
    { x: 0, y: 0, w: 10, h: 10 },
    { x: 20, y: 0, w: 10, h: 10 },
  ];
  assert.equal(minSeparation(rects), 10);
  const shrunk = shrinkRects(rects, 0.5);
  assert.equal(shrunk[0].w, 5);
  assert.equal(shrunk[0].x + shrunk[0].w / 2, 5, "中心不动");
  assert.equal(minSeparation(shrunk), 15, "盒子变小 → 间距变大（这才是缩放的正确方向）");
});

test("fitUniformScale：本来就够宽 → 1.0；太挤 → 按 1% 步长降且满足 gap", () => {
  const roomy = [
    { x: 0, y: 0, w: 10, h: 10 },
    { x: 40, y: 0, w: 10, h: 10 },
  ];
  assert.equal(fitUniformScale(roomy, { gap: 8 }).scale, 1);

  // 中心距 11、盒宽 10、要求间隙 4 → 11 - 10s ≥ 4 → s ≤ 0.7
  const tight = [
    { x: 0, y: 0, w: 10, h: 10 },
    { x: 11, y: 0, w: 10, h: 10 },
  ];
  const fit = fitUniformScale(tight, { gap: 4 });
  assert.ok(fit.scale < 1, "挤的时候必须缩");
  assert.equal(fit.scale, 0.7, "落在 1% 刻度上的最大可行缩放");
  assert.ok(fit.minGap >= 4 - 1e-9, `缩放后间距应 ≥ gap，实际 ${fit.minGap}`);

  // 缩到下限仍不够：如实报告不可行，而不是继续缩
  const hopeless = fitUniformScale(tight, { gap: 8, floor: 0.5 });
  assert.equal(hopeless.fits, false);
  assert.equal(hopeless.scale, 0.5);
});

test("clamp / quantizeDown：下限与步长语义", () => {
  assert.equal(clamp(0.2, 0.5, 1), 0.5);
  assert.equal(clamp(1.4, 0.5, 1), 1);
  assert.equal(quantizeDown(0.634, 0.01), 0.63);
  assert.equal(quantizeDown(1, 0.01), 1);
});

// ── 环形牌桌 ────────────────────────────────────────────────
test("ringRadii：窄设计框下收窄半径（不裁切）而不是硬撑", () => {
  const wide = ringRadii({ tableRx: 500, tableRy: 400, seatW: 160, seatH: 176, designW: 1200, designH: 900 });
  const narrow = ringRadii({ tableRx: 500, tableRy: 400, seatW: 160, seatH: 176, designW: 640, designH: 900 });
  assert.ok(narrow.rx < wide.rx, "设计框变窄时半径必须跟着收");
  assert.ok(narrow.rx >= 0);
});

test("ringLayout：8 席不重叠、不裁切", () => {
  const layout = ringLayout({ count: 8, designW: 1200, designH: 900, seatW: 160, seatH: 176 });
  assert.equal(layout.overlap, false, "8 席不应重叠");
  assert.equal(layout.clipped, false, "8 席不应被裁切");
  assert.ok(layout.scale >= SCALE_FLOOR && layout.scale <= 1);
  assert.equal(layout.rects.length, 8);
  assert.ok(withinBounds(layout.rects, 1200, 900));
});

test("ringLayout：10 席要么不重叠不裁切，要么缩到下限并如实报告", () => {
  const layout = ringLayout({ count: 10, designW: 1200, designH: 900, seatW: 160, seatH: 176 });
  assert.equal(layout.rects.length, 10);
  if (layout.fits) {
    assert.equal(layout.overlap, false, "10 席可行时必须不重叠");
    assert.equal(layout.clipped, false, "10 席可行时必须不裁切");
  } else {
    assert.ok(layout.scale <= SCALE_FLOOR + 1e-9, "不可行时应缩到下限而不是无限缩");
  }
});

test("ringLayout：席数越多缩放越小（单调不增）", () => {
  const small = ringLayout({ count: 6, designW: 1200, designH: 900, seatW: 160, seatH: 176 });
  const big = ringLayout({ count: 12, designW: 1200, designH: 900, seatW: 160, seatH: 176 });
  assert.ok(big.scale <= small.scale, `12 席(${big.scale}) 不应比 6 席(${small.scale}) 更大`);
});

test("ringRects / ringLayout 空席为 0", () => {
  assert.deepEqual(ringRects({ count: 0 }), []);
  const layout = ringLayout({ count: 0 });
  assert.equal(layout.rects.length, 0);
  assert.equal(layout.overlap, false);
});

// ── 纵向座位列（当前牌桌形态）────────────────────────────────
test("columnLayout：8 席在 700px 内放下且不重叠", () => {
  const layout = columnLayout({ count: 8, availableHeight: 700, rowHeight: 132, gap: 9 });
  assert.ok(layout.scale < 1, "8 席 × 132px 放不进 700px，必须缩");
  assert.ok(layout.total <= 700, `总高 ${layout.total} 应 ≤ 700`);
  assert.ok(layout.minSeparation >= 0, "行间不许重叠");
  assert.equal(layout.needsScroll, false);
  assert.ok(layout.rowHeight >= 44, "行高不得低于可读下限");
});

test("columnLayout：空间充裕时保持 1.0（不改原观感）", () => {
  const layout = columnLayout({ count: 8, availableHeight: 2000, rowHeight: 132, gap: 9 });
  assert.equal(layout.scale, 1);
  assert.equal(layout.rowHeight, 132);
  assert.equal(layout.gap, 9);
  assert.equal(layout.total, 8 * 132 + 7 * 9);
});

test("columnLayout：缩到下限仍放不下 → 明确要求滚动，不硬缩", () => {
  const layout = columnLayout({ count: 10, availableHeight: 320, rowHeight: 132, gap: 9, minRowHeight: 44 });
  assert.ok(layout.scale <= 0.5 + 1e-9, `应落到下限附近，实际 ${layout.scale}`);
  assert.equal(layout.needsScroll, true);
  assert.ok(layout.rowHeight >= 44);
});

test("columnLayout：0 席与未知高度都安全", () => {
  assert.equal(columnLayout({ count: 0, availableHeight: 700 }).scale, 1);
  const unknown = columnLayout({ count: 8, availableHeight: 0, rowHeight: 132, gap: 9 });
  assert.equal(unknown.scale, 1, "拿不到高度时不做猜测");
  assert.equal(unknown.fits, true);
});

test("columnRects：矩形按行堆叠，间距等于 gap", () => {
  const rects = columnRects({ count: 3, width: 320, rowHeight: 100, gap: 9 });
  assert.equal(rects.length, 3);
  assert.equal(rects[1].y, 109);
  assert.equal(minSeparation(rects), 9);
});

// ── 纯度约束：这个模块不许摸 DOM ────────────────────────────
test("模块本身无 DOM / 框架依赖（纯函数）", () => {
  const source = readFileSync(join(HERE, "../../src/lib/tableLayout.js"), "utf8");
  const code = source
    .split("\n")
    .filter((line) => !line.trim().startsWith("*") && !line.trim().startsWith("//"))
    .join("\n");
  for (const bad of ["document.", "window.", "localStorage", "requestAnimationFrame", "from \"vue\"", "from 'vue'"]) {
    assert.ok(!code.includes(bad), `纯函数模块不该出现 ${bad}`);
  }
});

// ── 运行 ───────────────────────────────────────────────────
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
console.log(`\n${cases.length - failed}/${cases.length} passed（SCALE_STEP=${SCALE_STEP}, SCALE_FLOOR=${SCALE_FLOOR}, SEAT_GAP=${SEAT_GAP}）`);
if (failed) process.exit(1);
