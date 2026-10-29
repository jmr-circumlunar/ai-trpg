/**
 * 极简路由（不引 vue-router）。
 *
 * 页面只有四个，路径即状态：
 *   `/`              大厅（世界选择）
 *   `/world/<id>`    世界详情（存档槽 + 新建）
 *   `/setup`         房间（号位 × agent，开局）
 *   `/play`          对局（原来的玩家页，`?sid=` 指定存档）
 *
 * 为什么不引路由库：构建产物多一个 40KB 依赖，换来的只是几个路径匹配；
 * 而且我们**不做**客户端 history 接管（刷新就 404），所以只读 `location`。
 */

export function currentRoute() {
  const path = location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/") return { name: "lobby" };
  if (path === "/setup") return { name: "setup" };
  if (path === "/agents") return { name: "agents" };
  if (path === "/play") return { name: "play" };
  const agentsRoute = path.match(/^\/world\/([^/]+)\/agents$/);
  if (agentsRoute) return { name: "world-agents", id: decodeURIComponent(agentsRoute[1]) };
  const saves = path.match(/^\/world\/([^/]+)\/saves$/);
  if (saves) return { name: "saves", id: decodeURIComponent(saves[1]) };
  const matched = path.match(/^\/world\/([^/]+)$/);
  if (matched) return { name: "world", id: decodeURIComponent(matched[1]) };
  // 其它路径（含旧的 /characters、/trace、/gm）继续交给对局页/静态页处理
  return { name: "play" };
}

export function query(name) {
  return new URLSearchParams(location.search).get(name) || "";
}

export function go(path) {
  location.href = path;
}

/**
 * 复盘页地址。
 *
 * `/trace` 是**静态页**（不是 Vue 路由），它自己从 `location.search` 读 `sid`，
 * 所以必须在链接里带上——对局页以前直接写 `href="/trace"`，点进去就是
 * 「缺少 ?sid= 参数」的错误页（G9 bug 2：不报错，只是一键不可用）。
 */
export function tracePath(sid) {
  const id = String(sid || "").trim();
  return id ? `/trace?sid=${encodeURIComponent(id)}` : "/trace";
}

