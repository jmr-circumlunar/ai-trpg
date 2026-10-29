/**
 * 世界上下文（方案 37）：**所有页面取世界侧数据的唯一入口**。
 *
 * 为什么要有这一层：上一轮六个页面一起动，每个页面都自己 `fetch` 世界数据、
 * 自己拼资产路径（`/cards/xxx`、`/theme.css`、`/hero`），结果：
 *   ① 同一页多次请求 → 可能半新半旧；
 *   ② 页面之间互相牵连 → 改一处牵动全部；
 *   ③ 前端知道了后端路由结构 → 换世界要改代码。
 *
 * 收口之后：**页面只调 `loadContext(worldId)`，其它什么都不做**。
 * 缓存与 in-flight 去重都在这里，换世界时 `clearContext()` 即可。
 */
import { getJson } from "./api.js";

const CACHE = new Map();     // worldId → context
const INFLIGHT = new Map();  // worldId → Promise（同一世界的并发请求只发一次）

export async function loadContext(worldId) {
  if (!worldId) return null;
  if (CACHE.has(worldId)) return CACHE.get(worldId);
  if (INFLIGHT.has(worldId)) return INFLIGHT.get(worldId);

  const promise = getJson(`/api/worlds/${encodeURIComponent(worldId)}/context`)
    .then((ctx) => {
      CACHE.set(worldId, ctx);
      INFLIGHT.delete(worldId);
      return ctx;
    })
    .catch((err) => {
      INFLIGHT.delete(worldId);
      throw err;
    });
  INFLIGHT.set(worldId, promise);
  return promise;
}

export function contextOf(worldId) {
  return CACHE.get(worldId) || null;
}

export function clearContext(worldId) {
  if (worldId) CACHE.delete(worldId);
  else CACHE.clear();
}

/** 牌面 URL：来自 context 的资产清单（前端不拼路径、不认世界语义）。 */
export function cardUrl(context, cardId) {
  const list = (context && context.ui && context.ui.cards) || [];
  const hit = list.find((item) => item.id === cardId);
  return hit ? hit.url : "";
}

/** 身份 → 牌面 id（世界包在 `ui.card_images` 里声明）。 */
export function cardIdOf(context, profession) {
  const map = (context && context.ui && context.ui.card_images) || {};
  return map[profession] || "";
}

/** 主题 CSS 地址（世界包自带；没有就没有主题）。 */
export function themeUrl(context) {
  return (context && context.ui && context.ui.theme_url) || "";
}
