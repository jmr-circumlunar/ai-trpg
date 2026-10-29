/**
 * 牌桌布局数学（**纯函数**：无 DOM、无框架依赖、无计时器）。
 *
 * 为什么要有这个文件：布局原先写在组件内联样式里 —— 「凭观感调 CSS」的后果是
 * G9 里 13 条痛点全都无法回归（改一个值，不知道会不会把另一处挤坏）。
 * 把「座位怎么摆、缩到多小」抽成可单测的数学，才能在 8 席 / 10 席 / 窄屏下
 * 断言「不重叠、不裁切、字不变形」。
 *
 * 三条边界：
 * 1. 只做几何计算：输入数字与矩形，输出数字与矩形；不读 DOM、不读世界包。
 * 2. 尺度参数（`step` / `floor` / `gap`）是**视觉参数**，由调用方传入，模块不写死「我们这一版」的值。
 * 3. 缩放是**重算框尺寸后重排**，不是 CSS `transform: scale()`
 *    —— 后者会把字一起缩小（不可读），我们只缩盒子、不缩字号。
 *
 * 算法形态借鉴 `@stargazeryan/poker-ui`（MIT）的 `src/layout.ts::fitSeatScale`
 * 与 `src/geometry.ts`：椭圆等分 → 两两最小间距 → 以 1% 步长从 1.0 降到下限，
 * 取「所有相邻座位都满足最小间隙」的最大统一缩放。见知识库
 * `07-AI项目/AI跑团/过程记录/参考/01-前端视觉与交互借鉴.md` §A-1。
 */

/** 缩放的搜索步长（1%），与借鉴来源一致。 */
export const SCALE_STEP = 0.01;
/** 缩放下限：再小就看不清了，宁可让容器滚动。 */
export const SCALE_FLOOR = 0.5;
/** 座位之间的最小间隙（px）。 */
export const SEAT_GAP = 8;

const DEG = Math.PI / 180;

/** 夹到 `[lo, hi]`。 */
export function clamp(value, lo, hi) {
  const v = Number(value);
  if (!Number.isFinite(v)) return lo;
  return Math.min(hi, Math.max(lo, v));
}

/** 向下取整到 `step` 的整数倍（避免浮点噪声把 0.63 变成 0.62）。 */
export function quantizeDown(value, step = SCALE_STEP) {
  const s = Number(step) > 0 ? Number(step) : SCALE_STEP;
  return Math.floor(Number(value) / s + 1e-9) * s;
}

/** 椭圆上第 `index` 个等分点（角度从 `startAngleDeg` 起，默认 12 点方向）。 */
export function ringPoint(index, count, options = {}) {
  const { cx = 0, cy = 0, rx = 1, ry = 1, startAngleDeg = -90 } = options;
  const n = Math.max(1, Math.trunc(Number(count)) || 1);
  const stepDeg = 360 / n;
  const angleDeg = startAngleDeg + index * stepDeg;
  const angle = angleDeg * DEG;
  return {
    index,
    angleDeg: ((angleDeg % 360) + 360) % 360,
    x: cx + rx * Math.cos(angle),
    y: cy + ry * Math.sin(angle),
  };
}

/** 以点为中心、给定宽高的矩形（左上角坐标）。 */
export function rectAtCenter(point, width, height) {
  return {
    x: point.x - width / 2,
    y: point.y - height / 2,
    w: width,
    h: height,
  };
}

/**
 * 两两之间的**最小轴对齐分离量**（负值 = 重叠量）。
 *
 * 轴对齐盒子的分离量是「横向间隙」与「纵向间隙」取大：只要有一个方向分开就算不重叠。
 * O(n²)，8–10 席下可忽略（借鉴来源同样如此）。
 */
export function minSeparation(rects) {
  const list = rects || [];
  if (list.length < 2) return Infinity;
  let best = Infinity;
  for (let i = 0; i < list.length; i += 1) {
    for (let j = i + 1; j < list.length; j += 1) {
      const a = list[i];
      const b = list[j];
      const gapX = Math.max(a.x - (b.x + b.w), b.x - (a.x + a.w));
      const gapY = Math.max(a.y - (b.y + b.h), b.y - (a.y + a.h));
      best = Math.min(best, Math.max(gapX, gapY));
    }
  }
  return best;
}

/**
 * 统一缩放一组矩形的**尺寸**，位置（中心）保持不动。
 *
 * 这是座位自动缩放的正确原语：座位挂在固定的环位/行位上，缩的是盒子，
 * 盒子变小、包络不变 → 两两间距**变大**。若连位置一起缩（围绕牌桌中心），
 * 间距会同比例变小，缩了反而更挤 —— 那是错的模型，早期实现踩过。
 */
export function shrinkRects(rects, scale) {
  const s = clamp(scale, 0.01, 4);
  return (rects || []).map((r) => {
    const cx = r.x + r.w / 2;
    const cy = r.y + r.h / 2;
    const w = r.w * s;
    const h = r.h * s;
    return { x: cx - w / 2, y: cy - h / 2, w, h };
  });
}

/** 矩形是否都落在设计框内（不裁切）。 */
export function withinBounds(rects, width, height, epsilon = 0.5) {
  return (rects || []).every(
    (r) =>
      r.x >= -epsilon &&
      r.y >= -epsilon &&
      r.x + r.w <= width + epsilon &&
      r.y + r.h <= height + epsilon
  );
}

/**
 * 座位环半径：在「桌沿之内」与「设计框之内」之间取小，保证最外圈座位不被裁掉。
 *
 * 借鉴来源的注释直说了这是修「窄布局下最左/右/上/下座位被裁」的补丁；
 * 这里多留一个 `edgePad`，因为我们的卡还有阴影与描边。
 */
export function ringRadii(options = {}) {
  const {
    tableRx = 0,
    tableRy = 0,
    seatW = 0,
    seatH = 0,
    feltGap = 10,
    edgePad = 6,
    designW = 0,
    designH = 0,
  } = options;
  const rx = Math.min(tableRx - feltGap + seatW / 2, designW / 2 - seatW / 2 - edgePad);
  const ry = Math.min(tableRy - feltGap + seatH / 2, designH / 2 - seatH / 2 - edgePad);
  return { rx: Math.max(0, rx), ry: Math.max(0, ry) };
}

/** 一组环形座位的矩形（未缩放）。 */
export function ringRects(options = {}) {
  const {
    count = 0,
    cx = 0,
    cy = 0,
    rx = 0,
    ry = 0,
    seatW = 0,
    seatH = 0,
    startAngleDeg = -90,
  } = options;
  const n = Math.max(0, Math.trunc(Number(count)) || 0);
  const out = [];
  for (let i = 0; i < n; i += 1) {
    const point = ringPoint(i, n, { cx, cy, rx, ry, startAngleDeg });
    out.push(rectAtCenter(point, seatW, seatH));
  }
  return out;
}

/**
 * 以 1% 步长从 1.0 往下找「所有座位两两间距 ≥ gap」的最大统一缩放。
 *
 * 为什么是步进搜索而不是解不等式：环上相邻座位的间距是角度的非线性函数，
 * 解析解不划算；1% 步长在 100 次以内收敛，且结果稳定可复现（便于单测）。
 */
export function fitUniformScale(rects, options = {}) {
  const { gap = SEAT_GAP, floor = SCALE_FLOOR, step = SCALE_STEP } = options;
  const list = rects || [];
  if (list.length < 2) return { scale: 1, minGap: Infinity, steps: 0, fits: true };
  const lo = clamp(floor, 0.05, 1);
  let steps = 0;
  for (let scale = 1; scale >= lo - 1e-9; scale -= step) {
    const s = Math.max(lo, quantizeDown(scale, step));
    const minGap = minSeparation(shrinkRects(list, s));
    steps += 1;
    if (minGap >= gap - 1e-9) {
      return { scale: Number(s.toFixed(4)), minGap, steps, fits: true };
    }
  }
  const minGap = minSeparation(shrinkRects(list, lo));
  return { scale: Number(lo.toFixed(4)), minGap, steps: steps + 1, fits: minGap >= gap - 1e-9 };
}

/** 环形牌桌完整布局：半径 → 座位矩形 → 自动缩放，并报告是否满足不重叠/不裁切。 */
export function ringLayout(options = {}) {
  const {
    count = 0,
    designW = 1200,
    designH = 900,
    seatW = 160,
    seatH = 176,
    gap = SEAT_GAP,
    floor = SCALE_FLOOR,
    step = SCALE_STEP,
    inset = 24,
    feltGap = 10,
    edgePad = 6,
    startAngleDeg = -90,
  } = options;
  const n = Math.max(0, Math.trunc(Number(count)) || 0);
  const scaledSeatW = seatW;
  const scaledSeatH = seatH;
  const radii = ringRadii({
    tableRx: designW / 2 - inset,
    tableRy: designH / 2 - inset,
    seatW: scaledSeatW,
    seatH: scaledSeatH,
    feltGap,
    edgePad,
    designW,
    designH,
  });
  const raw = ringRects({
    count: n,
    cx: designW / 2,
    cy: designH / 2,
    rx: radii.rx,
    ry: radii.ry,
    seatW: scaledSeatW,
    seatH: scaledSeatH,
    startAngleDeg,
  });
  const fit = fitUniformScale(raw, { gap, floor, step });
  const rects = shrinkRects(raw, fit.scale);
  return {
    count: n,
    radii,
    scale: fit.scale,
    minGap: fit.minGap,
    fits: fit.fits,
    overlap: fit.minGap < 0,
    clipped: !withinBounds(rects, designW, designH),
    rects,
  };
}

/**
 * 纵向座位列的矩形（未缩放）：我们当前的牌桌是「一行一角色」，
 * 不是环形 —— 但「统一缩放 + 下限」的约束同样适用。
 */
export function columnRects(options = {}) {
  const { count = 0, width = 320, rowHeight = 132, gap = 9 } = options;
  const n = Math.max(0, Math.trunc(Number(count)) || 0);
  const out = [];
  let y = 0;
  for (let i = 0; i < n; i += 1) {
    out.push({ x: 0, y, w: width, h: rowHeight });
    y += rowHeight + gap;
  }
  return out;
}

/**
 * 纵向座位列的自动缩放：解「总高 ≤ 可用高」，向下取到 1% 步长，并夹在
 * `[floor, 1]` 与「行高不低于 `minRowHeight`」之间。
 *
 * 为什么不复用 `fitUniformScale`：纵向列里相邻行在正常流里**本来就**不重叠，
 * 真正的约束是总高而不是两两间距；把间距当约束会得出「永远缩放 1.0」的假结论。
 * 两者共用同一个下限与步长语义（floor + 1%），只是判定式不同。
 */
export function columnLayout(options = {}) {
  const {
    count = 0,
    availableHeight = 0,
    rowHeight = 132,
    gap = 9,
    floor = SCALE_FLOOR,
    step = SCALE_STEP,
    minRowHeight = 44,
    width = 320,
  } = options;
  const n = Math.max(0, Math.trunc(Number(count)) || 0);
  if (!n) {
    return {
      count: 0,
      scale: 1,
      rowHeight,
      gap,
      total: 0,
      fits: true,
      minSeparation: Infinity,
      needsScroll: false,
    };
  }
  const avail = Math.max(0, Number(availableHeight) || 0);
  const need = n * rowHeight + (n - 1) * gap;
  const lo = Math.max(clamp(floor, 0.05, 1), clamp(minRowHeight / rowHeight, 0.05, 1));
  const raw = avail > 0 ? avail / need : 1;
  const scale = avail > 0 ? quantizeDown(clamp(raw, lo, 1), step) : 1;
  const finalScale = Math.max(lo, Math.min(1, scale));
  const scaledRow = Math.max(1, Math.round(rowHeight * finalScale));
  const scaledGap = Math.max(0, Math.round(gap * finalScale));
  const total = n * scaledRow + (n - 1) * scaledGap;
  const rects = columnRects({ count: n, width, rowHeight: scaledRow, gap: scaledGap });
  return {
    count: n,
    scale: Number(finalScale.toFixed(2)),
    rowHeight: scaledRow,
    gap: scaledGap,
    total,
    fits: avail > 0 ? total <= avail : true,
    minSeparation: minSeparation(rects),
    // 缩到下限仍放不下：告诉调用方「需要滚动」而不是硬缩到看不清
    needsScroll: avail > 0 ? total > avail : false,
  };
}
