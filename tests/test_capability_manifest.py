"""C59 能力清单：六类注册必须齐全且自洽。

清单是「世界包该怎么配」的唯一真相，因此这里断言的不是实现细节，
而是「清单本身能不能被世界包作者信任」：每个 kind 有参数、每个系统有状态、
每个谓词能说出依赖哪些系统、每个可写字段能说出由哪个系统提供。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.rulespack import manifest  # noqa: E402

GENERIC_KINDS = {
    "narration",
    "action",
    "skill",
    "collective",
    "apply",
    "check",
    "broadcast",
    "commit",
}
VALID_PARAM_TYPES = {"str", "int", "bool", "list", "dict", "enum"}


def test_phase_kinds_are_exactly_the_generic_eight():
    assert set(manifest.phase_kinds) == GENERIC_KINDS


def test_every_phase_kind_declares_params_with_type_and_default():
    for kind, spec in manifest.phase_kinds.items():
        assert spec.params, f"{kind} 没有声明任何参数"
        for name, param in spec.params.items():
            assert param.type in VALID_PARAM_TYPES, f"{kind}.{name} 类型未声明或不合法"
            if param.type == "enum":
                assert param.values, f"{kind}.{name} 是 enum 但没给取值集合"
                assert param.default in param.values, f"{kind}.{name} 默认值不在枚举内"


def test_every_phase_kind_has_summary_for_world_authors():
    for kind, spec in manifest.phase_kinds.items():
        assert spec.summary.strip(), f"{kind} 缺少一句话说明（世界包作者靠它理解用途）"


def test_every_system_declares_status():
    assert manifest.systems, "系统注册表为空"
    for sid, spec in manifest.systems.items():
        assert spec.id == sid
        assert spec.status in {"planned", "active", "deprecated"}, f"{sid} status 不合法"


def test_no_unknown_system_referenced():
    for sid, spec in manifest.systems.items():
        for dep in (*spec.requires, *spec.conflicts):
            assert dep in manifest.systems, f"{sid} 引用了未知系统 {dep}"


def test_planned_system_cannot_be_required_or_required_by_active():
    for sid, spec in manifest.systems.items():
        if spec.status == "planned":
            assert not spec.required, f"{sid} 已预留未实现，不能声明 required"


def test_predicates_are_eleven_and_declare_needs():
    # 10 个通用谓词 + B57 的 `win_decided`（读 state.winner，不依赖系统开关）
    assert len(manifest.predicates) == 11
    for name, spec in manifest.predicates.items():
        assert isinstance(spec.needs, tuple), f"{name}.needs 必须是 tuple"
        assert spec.summary.strip(), f"{name} 缺少说明"
        for dep in spec.needs:
            assert dep in manifest.systems, f"谓词 {name} 依赖未知系统 {dep}"
            assert manifest.systems[dep].status == "active", (
                f"谓词 {name} 依赖未实现的系统 {dep}，会导致条件永远为假"
            )


def test_tools_map_to_systems():
    assert manifest.tools["recall"].requires == ("long_memory",)
    assert manifest.tools["look"].requires == ()
    assert manifest.tools["check_self"].requires == ()


def test_delta_fields_can_name_their_owning_system():
    assert manifest.delta_fields["enemies"] == "combat"
    assert manifest.delta_fields["characters.*.inventory"] == "equipment"
    assert manifest.delta_fields["clock.*"] == "time"
    # 不受任何系统控制的基础字段用 None 表示「恒可写」
    assert manifest.delta_fields["tension"] is None
    for prefix, owner in manifest.delta_fields.items():
        if owner is not None:
            assert owner in manifest.systems, f"delta 前缀 {prefix} 指向未知系统 {owner}"


def test_ui_blocks_reference_known_systems():
    assert manifest.ui_blocks
    for block in manifest.ui_blocks:
        assert block.sel, "区块缺少选择器"
        for dep in block.requires:
            assert dep in manifest.systems, f"区块 {block.sel} 依赖未知系统 {dep}"


def test_world_fields_cover_the_five_declarations():
    names = {f.name for f in manifest.world_fields}
    assert {"systems", "turn_flow", "dice", "memory", "prompts"} <= names
    for f in manifest.world_fields:
        assert f.default is not None or f.required, f"{f.name} 既非必填也没有默认值"


def test_manifest_is_exportable_as_world_author_doc():
    from tools.describe_capabilities import render_markdown

    text = render_markdown()
    assert "## 系统开关" in text
    assert "## 阶段种类" in text
    assert "## 阶段谓词" in text
    assert "## Agent 工具" in text
    assert "## 可写状态字段" in text
    assert "## 前端区块" in text
    assert "## 世界包文件" in text
    for sid in manifest.systems:
        assert f"`{sid}`" in text, f"导出文档漏了系统 {sid}"
    for kind in manifest.phase_kinds:
        assert f"`{kind}`" in text, f"导出文档漏了阶段种类 {kind}"


def test_export_is_deterministic():
    from tools.describe_capabilities import render_markdown

    assert render_markdown() == render_markdown()


# ── 胜负判法清单（C58） ─────────────────────────────────────

GENERIC_WIN_KINDS = {
    "all_eliminated",
    "camp_eliminated",
    "camp_superiority",
    "group_eliminated",
}


def test_win_kinds_are_exactly_the_generic_four():
    assert set(manifest.win_kinds) == GENERIC_WIN_KINDS


def test_every_win_kind_has_summary_and_param_types():
    for kind, spec in manifest.win_kinds.items():
        assert spec.kind == kind
        assert spec.summary.strip(), f"{kind} 缺少一句话说明"
        for name, param in spec.params.items():
            assert param.type in VALID_PARAM_TYPES, f"{kind}.{name} 类型未声明或不合法"


def test_every_win_kind_has_an_implementation():
    """清单登记了但 `ending.py` 没判据 = 世界包选了却不生效（静默失效）。"""
    from trpg.engine import ending

    source = inspect.getsource(ending)
    for kind in manifest.win_kinds:
        assert f'"{kind}"' in source, f"{kind} 在清单里，但 ending.py 没有对应判据"


def test_only_group_rule_needs_identity_table():
    """需要身份表的判法必须显式声明：调用方据此决定传不传 `cards`。"""
    needs = {kind for kind, spec in manifest.win_kinds.items() if spec.needs_cards}
    assert needs == {"group_eliminated"}


def test_export_documents_every_win_kind():
    from tools.describe_capabilities import render_markdown

    text = render_markdown()
    assert "## 胜负条件" in text
    for kind in manifest.win_kinds:
        assert f"`{kind}`" in text, f"导出文档漏了胜负判法 {kind}"


def test_export_documents_turn_flow_defaults():
    """`declare:` 的可继承键必须在导出文档里，否则世界包作者不知道能写什么。"""
    from tools.describe_capabilities import render_markdown

    text = render_markdown()
    assert "## 阶段缺省值（`declare:`）" in text
    for name in manifest.turn_flow_defaults:
        assert f"`{name}`" in text, f"导出文档漏了 declare 键 {name}"


def test_export_documents_skill_registries():
    """技能三类取值域必须在导出文档里，否则世界包作者不知道能写什么。"""
    from tools.describe_capabilities import render_markdown

    text = render_markdown()
    assert "## 主动技能" in text
    for name in manifest.skill_triggers:
        assert f"`{name}`" in text, f"导出文档漏了触发时机 {name}"
    for name in manifest.skill_effects:
        assert f"`{name}`" in text, f"导出文档漏了效果原语 {name}"
    for name in manifest.skill_targets:
        assert f"`{name}`" in text, f"导出文档漏了可选目标 {name}"
