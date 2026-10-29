/**
 * 布局骨架分派（方案 37）：**世界包说用哪个骨架，就用哪个骨架**。
 *
 * 换世界只改 `world.yaml: ui.layout`，不改页面代码。
 * 未知布局回落 `flow`（通用跑团流）并告警——不静默换，也不报错卡住。
 */
import FlowLayout from "../layouts/FlowLayout.vue";
import TabletopLayout from "../layouts/TabletopLayout.vue";

const LAYOUTS = {
  tabletop: TabletopLayout, // 牌桌：左角色列 + 中间时间线
  flow: FlowLayout,         // 通用跑团：左名单 + 中内容 + 右信息
  board: FlowLayout,        // 角色卡布局（尚未单独实现，先用通用流）
};

export function resolveLayout(context) {
  const name = (context && context.ui && context.ui.layout) || "flow";
  const component = LAYOUTS[name];
  if (!component) {
    console.warn(`[layout] 未知布局「${name}」，回落到 flow`);
    return FlowLayout;
  }
  return component;
}

export const LAYOUT_NAMES = Object.keys(LAYOUTS);
