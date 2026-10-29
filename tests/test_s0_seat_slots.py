"""S0-3：座位插槽清单 + 中心公共区的回归入口。

三层钉：

1. **纯函数行为**：`node tests/js/seat_slots.test.mjs`（13 条：槽词表、折叠清单、
   存活优先与席号、未知状态不藏人、空输入不炸、纯度）。
2. **接线契约**：座位列必须真的按清单渲染（`v-for` + 七个槽），角色卡标头搬进行级
   `header` 槽后不许重复渲染两份。
3. **出口判据**：出局可折叠、公共区有独立组件、契约 id 一个不丢。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
JS_TEST = ROOT / "tests" / "js" / "seat_slots.test.mjs"

NODE = shutil.which("node")


def _read(*parts: str) -> str:
    return (SRC.joinpath(*parts)).read_text(encoding="utf-8")


def test_slot_module_exists_and_is_framework_free():
    mod = (SRC / "lib" / "seatSlots.js").read_text(encoding="utf-8")
    for want in ("SEAT_SLOT_ORDER", "export function seatSlotList", "export function planSeats"):
        assert want in mod, f"插槽清单模块缺少 {want}"
    for slot in ("above", "header", "children", "badge", "overlay", "below", "footer"):
        assert f'"{slot}"' in mod, f"槽词表缺少 {slot}"
    code = "\n".join(
        line
        for line in mod.splitlines()
        if not line.strip().startswith("*") and not line.strip().startswith("//")
    )
    for bad in ('from "vue"', "document.", "window."):
        assert bad not in code, f"纯函数模块不该依赖 {bad}"


def test_seat_column_renders_the_slot_list():
    """座位是**插槽清单**：模板按清单渲染，七个槽各有落点，外部可用同名 v-slot 覆盖。"""
    seat = _read("components", "SeatColumn.vue")
    assert "planSeats" in seat and "seatSlots.js" in seat, "座位列要用插槽清单模块"
    assert "v-for=\"name in row.slots\"" in seat, "模板要按清单逐层渲染，而不是写死两块"
    for token in (
        "name === 'above'",
        "name === 'header'",
        "name === 'children'",
        "name === 'badge'",
        "name === 'overlay'",
        "name === 'below'",
        "name === 'footer'",
    ):
        assert token in seat, f"清单里的槽没有落点：{token}"
    # 每个槽都要能被子组件覆盖（具名 slot + 默认内容当 fallback）
    assert seat.count("<slot") >= 7, "七个槽都要留具名插槽给外部接管"


def test_seat_header_is_not_rendered_twice():
    """标头搬进行级 `header` 槽后，卡身不能再渲染一份（否则一行两个标题）。"""
    seat = _read("components", "SeatColumn.vue")
    assert ':show-head="false"' in seat, "插槽化座位要关掉卡自带的标头"
    card = _read("components", "SeatCards.vue")
    assert "showHead" in card and "v-if=\"showHead\"" in card, (
        "角色卡要能用 showHead 关掉标头，且默认仍带头（独立使用时行为不变）"
    )
    assert "showNote" in card, "卡下注记也要能交给行级 `below` 槽"


def test_out_seats_are_foldable():
    """出口判据之一：左列不许有「3/4 是死区」——出局席必须能折叠成细行。"""
    seat = _read("components", "SeatColumn.vue")
    assert "expandedOut" in seat and "collapsed" in seat, "座位列要有出局折叠状态"
    assert "toggle-out" in seat, "折叠要有可点的开关（不是靠刷新）"
    css = _read("styles", "theme.css")
    assert ".seat-row-folded" in css, "折叠行要有自己的样式（细行）"
    assert ".seat-fold-toggle" in css, "折叠开关要有样式"


def test_ownership_link_between_bubble_and_card():
    """G9 痛点 4：气泡与卡片之间要有**视觉归属引导**（连接层 + 跨缝尖角）。"""
    seat = _read("components", "SeatColumn.vue")
    assert "seat-above" in seat and "seat-above-line" in seat, "行上方要有归属连接层"
    css = _read("styles", "theme.css")
    assert ".seat-body" in css, "思考与卡片的双列容器"
    # 尖角必须跨过列间距：右移量要够大，不能只伸 6-7px
    tail = [line for line in css.splitlines() if "right: -13px" in line]
    assert tail, "气泡尖角要跨过列间距去碰卡片（right 外扩）"


def test_center_stage_holds_public_info():
    """公共信息（报幕 / 公布 / 票型）要有自己的地盘，不再混在时间线流水里。"""
    stage = _read("components", "CenterStage.vue")
    for kind in ("banner", "broadcast", "vote"):
        assert kind in stage, f"公共区要显示 {kind}"
    layout = _read("layouts", "TabletopLayout.vue")
    assert "CenterStage" in layout, "牌桌骨架要挂中心公共区"
    assert "GodTimeline" in layout and "SeatColumn" in layout, "原有两区不能丢"
    css = _read("styles", "theme.css")
    assert ".center-stage" in css, "公共区要有样式"


def test_contract_ids_untouched():
    """改布局不许动契约 id（改之前先看 test_web_ui.py::INDEX_IDS）。"""
    page = _read("pages", "IndexPage.vue")
    for eid in ("advance", "viewer", "progress", "hmode"):
        assert f'id="{eid}"' in page, f"契约 id #{eid} 丢了"


def test_theme_scale_still_box_only():
    """S0-2 的约束继续成立：缩放只改盒子，不改字号。"""
    css = _read("styles", "theme.css")
    assert "var(--seat-scale, 1)" in css
    for line in css.splitlines():
        if "var(--seat-scale" in line:
            assert "font-size" not in line, f"缩放不许改字号：{line.strip()}"


@pytest.mark.skipif(NODE is None, reason="未安装 Node，跳过 JS 单测")
def test_node_unit_tests_pass():
    res = subprocess.run(
        [NODE, str(JS_TEST)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert res.returncode == 0, f"JS 单测失败：\n{res.stdout}\n{res.stderr}"
    assert "passed" in res.stdout
