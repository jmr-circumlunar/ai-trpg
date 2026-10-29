/**
 * 座位插槽清单（**纯函数**：无 DOM、无框架依赖、无世界语义）。
 *
 * 为什么要有这个文件：座位原先在 `SeatColumn.vue` 里写死成「思考块 + 角色卡」两个固定块，
 * 想加一个角标、一条卡下注记，就得动模板结构、动 CSS、动布局数学三处，且没有回归可依。
 * 把「一个座位由哪几层槽构成、哪些槽此刻该显示」抽成可单测的数据，模板只负责按清单渲染。
 *
 * 词汇沿用 `header / children / footer / badge / overlay / above / below` 这七个位置语义
 * （见知识库 `参考/01-前端视觉与交互借鉴.md` §A-1、§A-3 的 zone/slot 形态）：
 *
 * | 槽 | 语义 | 谁在用 |
 * |---|---|---|
 * | `above`  | 行上方**归属连接层**：把思考列与卡列在视觉上连成一行 | `SeatColumn` |
 * | `header` | 行标头：席号 / 名字 / 身份 / 在场状态 | `SeatColumn`（`SeatCards` 自带时可关） |
 * | `children` | 主体：思考气泡 + 角色卡 | `SeatColumn` |
 * | `badge` | 角标：出局 / 念头条数 | `SeatColumn` |
 * | `overlay` | 覆盖层：出局降饱和遮罩（不用虚线边框） | `SeatColumn` |
 * | `below` | 卡下注记：最近一条已确认信息 | `SeatColumn` |
 * | `footer` | 行尾操作：出局席展开/收起、查看档案 | `SeatColumn` |
 *
 * 三条边界：
 * 1. 只做「清单与排序」：输入角色数组，输出行数组与统计；不读 DOM、不读世界包。
 * 2. 不写死视觉数值：缩放、间距、行高都在 `tableLayout.js` / `theme.css`，这里只给结构。
 * 3. 认不出的一律按「在场」保守处理 —— 未知状态不能把人藏起来（藏错人比多显示一行更糟）。
 */

/** 槽的**固定顺序**：从上到下，渲染顺序即此序。 */
export const SEAT_SLOT_ORDER = [
  "above",
  "header",
  "children",
  "badge",
  "overlay",
  "below",
  "footer",
];

/** 槽的中文语义（命名可中文，语义对应上面七项；只用于 label / 调试）。 */
export const SEAT_SLOT_LABELS = {
  above: "归属连接",
  header: "座位标头",
  children: "主体",
  badge: "角标",
  overlay: "覆盖层",
  below: "卡下注记",
  footer: "行尾操作",
};

/**
 * 折叠行只保留这两层：标头（是谁）+ 角标（什么状态）。
 *
 * 不放行尾：折叠行如果再带一个「展开」按钮，5 个出局席就有 5 个按钮，
 * 比不折叠还吵（S0-3 截图验收发现）。展开/收起统一交给列尾那一个开关。
 */
export const FOLDED_SLOT_KEEP = ["header", "badge"];

const ALIVE_UNKNOWN = true;

/** 归一成字符串（null / undefined / 数字都安全）。 */
function text(value) {
  return typeof value === "string" ? value.trim() : value == null ? "" : String(value).trim();
}

/** 角色是否在场：只认明确的 `alive === false`，其余一律当在场。 */
export function isAlive(agent) {
  if (!agent || typeof agent !== "object") return ALIVE_UNKNOWN;
  if (agent.alive === false) return false;
  if (agent.alive === true) return true;
  return ALIVE_UNKNOWN;
}

/** 一张座位卡片的「结构视图」：把角色对象拍平成模板要用的字段。 */
export function seatView(agent, options = {}) {
  const { index = 0, thoughtCount = 0 } = options;
  const item = agent || {};
  const alive = isAlive(item);
  const notes = Array.isArray(item.notes) ? item.notes.filter((n) => text(n)) : [];
  return {
    key: text(item.id) || `seat-${index}`,
    id: text(item.id),
    // 席号取**原始序号**：排序换了位置，席号不能跟着变（玩家按席号叫人）
    no: index + 1,
    name: text(item.name),
    profession: text(item.profession),
    alive,
    thoughtCount: Math.max(0, Math.trunc(Number(thoughtCount)) || 0),
    lastNote: notes.length ? text(notes[notes.length - 1]) : "",
  };
}

/**
 * 单个座位的插槽清单。
 *
 * @param {object} seat  `seatView` 的产物
 * @param {object} [options]
 * @param {boolean} [options.collapsed] 是否折叠（出局席收起时只留标头/角标/行尾）
 * @returns {{key:string, no:number, cancelled:boolean, slots:string[]}}
 */
export function seatSlotList(seat = {}, options = {}) {
  const { collapsed = false } = options;
  const alive = seat.alive !== false;
  const hasThought = (Number(seat.thoughtCount) || 0) > 0;
  const hasNote = Boolean(text(seat.lastNote));

  const visible = {
    // 连接层只在「展开行」有意义：折叠行没有主体可连
    above: !collapsed,
    header: true,
    children: !collapsed,
    // 角标：出局状态必显；在场但有念头时显示条数
    badge: !alive || hasThought,
    // 覆盖层只盖出局席，且折叠行不盖（折叠行要可读）
    overlay: !alive && !collapsed,
    below: !collapsed && hasNote,
    // 行尾操作只给**展开的**出局席：折叠行连行尾都不要，否则一屏全是「展开」按钮
    footer: !alive && !collapsed,
  };

  const slots = SEAT_SLOT_ORDER.filter((name) => visible[name]);
  return {
    key: text(seat.key),
    no: Number(seat.no) || 0,
    collapsed: Boolean(collapsed),
    slots,
  };
}

/**
 * 整列的座位清单：**在场席按原序号在前，出局席按原序号在后**（存活优先）。
 *
 * 「存活优先 + 出局折叠」正是 G9 痛点 2 / 5 的解法：8 席里 6 席出局时，
 * 左列不该有 3/4 屏是「（没有念头）」的死区。
 *
 * @param {Array} agents 角色数组（顺序 = 席次顺序）
 * @param {object} [options]
 * @param {Record<string, number>} [options.thoughtCounts] 角色名 → 念头条数
 * @param {boolean} [options.expandedOut] 出局席是否展开（默认收起）
 * @returns {{rows:Array, stats:object}}
 */
export function planSeats(agents, options = {}) {
  const { thoughtCounts = {}, expandedOut = false } = options;
  const list = Array.isArray(agents) ? agents : [];

  const alive = [];
  const out = [];
  list.forEach((agent, index) => {
    const counts = thoughtCounts || {};
    const name = text(agent && agent.name);
    const view = seatView(agent, {
      index,
      thoughtCount: Number(counts[name] || 0),
    });
    // 出局席默认收起：只留标头/角标/行尾（细条），点一下再展开
    const collapsed = !view.alive && !expandedOut;
    (view.alive ? alive : out).push({
      ...view,
      collapsed,
      slots: seatSlotList(view, { collapsed }).slots,
    });
  });

  const rows = [...alive, ...out];
  return {
    rows,
    stats: {
      total: rows.length,
      alive: alive.length,
      out: out.length,
      // 折叠了几行：给「已出局 N 席」那条按钮用
      folded: out.filter((row) => row.collapsed).length,
      // 行数（缩放按这个算，不按总席数）
      visibleRows: rows.length,
    },
  };
}
