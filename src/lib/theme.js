/**
 * 世界主题（方案 35）：世界包自带 `ui/theme.css`，前端按需加载。
 *
 * 为什么走 `<link>` 而不是打进构建产物：主题属于**世界内容**，加一个世界就多一套皮肤，
 * 不该让前端重新构建。后端只暴露固定路径 `/api/worlds/<id>/theme.css`。
 *
 * 约定：切世界先移除旧主题再插新的，避免两套变量互相覆盖。
 */
const LINK_ID = "world-theme";

export function loadWorldTheme(url) {
  const old = document.getElementById(LINK_ID);
  if (old) old.remove();
  if (!url) return;
  const link = document.createElement("link");
  link.id = LINK_ID;
  link.rel = "stylesheet";
  link.href = url;
  // 世界没有主题（404）时不报错，样式回落到基础暗色
  link.onerror = () => link.remove();
  document.head.appendChild(link);
}
