"""S0-2：牌桌布局数学（`src/lib/tableLayout.js`）的回归入口。

两层钉：

1. **纯函数行为**：交给 `node tests/js/table_layout.test.mjs` 跑（16 条：椭圆等分、
   两两间距、1% 步长统一缩放、8/10 席、纵向列、纯度检查）。仓库没有 JS 测试运行时，
   也不为一个纯函数模块引框架，所以用零依赖的 `node:assert`。
2. **接线契约**：组件必须真的消费这个模块、真的把缩放写成 CSS 变量，
   且**不许**退回 `transform: scale()`（那会把字一起缩小）。

没装 Node 的环境（CI）跳过 JS 层，但仍跑接线断言。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
JS_TEST = ROOT / "tests" / "js" / "table_layout.test.mjs"

NODE = shutil.which("node")


def test_layout_module_exists_and_is_framework_free():
    mod = (SRC / "lib" / "tableLayout.js").read_text(encoding="utf-8")
    for want in ("export function ringPoint", "export function minSeparation",
                 "export function fitUniformScale", "export function columnLayout"):
        assert want in mod, f"布局模块缺少 {want}"
    for bad in ("from \"vue\"", "document.", "window."):
        code = "\n".join(
            line for line in mod.splitlines()
            if not line.strip().startswith("*") and not line.strip().startswith("//")
        )
        assert bad not in code, f"纯函数模块不该依赖 {bad}"


def test_seat_column_consumes_layout_module():
    seat = (SRC / "components" / "SeatColumn.vue").read_text(encoding="utf-8")
    assert "lib/tableLayout.js" in seat, "角色区必须用抽出来的布局数学"
    assert "--seat-scale" in seat, "缩放必须以 CSS 变量输出给样式层"
    # 只看代码行：注释里提到「不是 transform: scale()」是说明，不是实现
    code = "\n".join(
        line for line in seat.splitlines()
        if not line.strip().startswith("<!--") and not line.strip().startswith("//")
    )
    assert "transform: scale(" not in code, "禁止用 transform: scale() 整块缩小（字会一起变小）"


def test_theme_scales_boxes_not_fonts():
    theme = (SRC / "styles" / "theme.css").read_text(encoding="utf-8")
    assert "var(--seat-scale, 1)" in theme, "样式层要读缩放变量"
    # 缩放只作用于盒子的内边距/最小高度（长度 × 数字），不作用于 font-size
    for line in theme.splitlines():
        if "var(--seat-scale" in line:
            assert "font-size" not in line, f"缩放不许改字号：{line.strip()}"


def test_contract_ids_untouched():
    """S0-2 只动布局，契约 id 一个都不许改（改之前先看 test_web_ui.py）。"""
    page = (SRC / "pages" / "IndexPage.vue").read_text(encoding="utf-8")
    for eid in ("advance", "viewer", "progress", "hmode"):
        assert f'id="{eid}"' in page, f"契约 id #{eid} 丢了"


@pytest.mark.skipif(NODE is None, reason="未安装 Node，跳过 JS 单测")
def test_node_unit_tests_pass():
    res = subprocess.run(
        [NODE, str(JS_TEST)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        # 显式 utf-8：Windows 默认按 GBK 解码，node 输出里的中文/框线字符会直接炸
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert res.returncode == 0, f"JS 单测失败：\n{res.stdout}\n{res.stderr}"
    assert "passed" in res.stdout
