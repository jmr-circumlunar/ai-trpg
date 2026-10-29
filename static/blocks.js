/* 能力 → 区块 的映射（C52）。
 *
 * 后端只声明「本局开了哪些系统」（GET /api/sessions/{sid}/config），
 * UI 怎么摆由这张表决定：依赖系统没全开就隐藏，避免出现「空敌人栏 / 空目标下拉」这种
 * 「能启动但页面像坏了」的状态。
 *
 * 新增区块 = 这里加一行（sel + requires，requires 为空数组表示恒显示）。
 * 表的判据来自 trpg/rulespack/manifest.py: ui_blocks，两边要一起改。
 */
const BLOCKS = [
  { sel: '#hp-enemies', requires: ['combat'] },
  { sel: '#hp-target', requires: ['combat'] },
  { sel: '#hp-mode', requires: ['combat'] },
  { sel: '#hp-skill', requires: ['combat'] },
  { sel: '#chars', requires: [] },
  { sel: '#profile-drawer', requires: ['equipment'] },
  { sel: '#meta-char', requires: ['meta_growth'] },
  { sel: '#meta-spend', requires: ['meta_growth'] },
  { sel: '#promote-list', requires: ['equipment', 'meta_growth'] },
  { sel: '#human-panel', requires: ['human_seat'] },
  { sel: '#hmode', requires: ['human_seat'] },
];

function applyCapabilities(systems) {
  const on = new Set(systems || []);
  for (const block of BLOCKS) {
    const ok = block.requires.every((r) => on.has(r));
    document.querySelectorAll(block.sel).forEach((el) => {
      el.hidden = !ok;
    });
  }
}

async function loadCapabilities(sid) {
  try {
    const res = await fetch(`/api/sessions/${sid}/config`);
    if (!res.ok) return null;
    const data = await res.json();
    applyCapabilities(data.systems);
    return data;
  } catch (err) {
    console.warn('能力清单加载失败（页面按全量渲染）', err);
    return null;
  }
}

window.applyCapabilities = applyCapabilities;
window.loadCapabilities = loadCapabilities;
window.CAPABILITY_BLOCKS = BLOCKS;
