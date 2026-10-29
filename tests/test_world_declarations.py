"""B41 世界包装配声明校验：C46–C49 + kind 合法性 + 预留系统语义。

核心要求是**向后兼容**：两个存量世界包没有 systems/turn_flow/dice/memory，
校验必须先保证「无声明 ⇒ 0 问题」，再报新增的 5 类错误。
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.rulespack.validate import validate_declarations  # noqa: E402


def make_world(tmp_path: Path, files: dict[str, str], world_id: str = "wtest") -> Path:
    base = tmp_path / "worlds" / world_id
    base.mkdir(parents=True)
    (base / "world.yaml").write_text(
        f"world_id: {world_id}\nname: 测试\n", encoding="utf-8"
    )
    for rel, text in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return base


def test_no_declaration_is_clean(tmp_path):
    assert validate_declarations(make_world(tmp_path, {})) == []


def test_unknown_phase_kind(tmp_path):
    base = make_world(
        tmp_path,
        {"turn_flow.yaml": "phases:\n  - id: weird\n    kind: moonlight\n"},
    )

    errors = validate_declarations(base)

    assert any("kind" in e and "moonlight" in e for e in errors), errors


def test_phase_missing_kind(tmp_path):
    base = make_world(tmp_path, {"turn_flow.yaml": "phases:\n  - id: scene\n"})

    errors = validate_declarations(base)

    assert any("kind" in e and "scene" in e for e in errors), errors


def test_unknown_predicate(tmp_path):
    base = make_world(
        tmp_path,
        {
            "turn_flow.yaml": (
                "phases:\n  - id: fight\n    kind: action\n    when: enemies_nearby\n"
            )
        },
    )

    errors = validate_declarations(base)

    assert any("enemies_nearby" in e for e in errors), errors


def test_predicate_needs_system_enabled(tmp_path):
    base = make_world(
        tmp_path,
        {
            "systems.yaml": "systems:\n  map: { enabled: false }\n",
            "turn_flow.yaml": (
                "phases:\n"
                "  - id: ambush\n"
                "    kind: action\n"
                "    when: location_tag\n"
                "    tag: dark\n"
            ),
        },
    )

    errors = validate_declarations(base)

    assert any("location_tag" in e and "map" in e for e in errors), errors


def test_unknown_system(tmp_path):
    base = make_world(
        tmp_path, {"systems.yaml": "systems:\n  flying: { enabled: true }\n"}
    )

    assert any("flying" in e for e in validate_declarations(base))


def test_required_system_disabled(tmp_path):
    base = make_world(
        tmp_path, {"systems.yaml": "systems:\n  time: { enabled: false }\n"}
    )

    errors = validate_declarations(base)

    assert any("time" in e and "required" in e for e in errors), errors


def test_requires_not_closed(tmp_path):
    base = make_world(
        tmp_path,
        {
            "systems.yaml": (
                "systems:\n"
                "  combat: { enabled: true }\n"
                "  equipment: { enabled: false }\n"
            )
        },
    )

    errors = validate_declarations(base)

    assert any("combat" in e and "equipment" in e for e in errors), errors


def test_planned_system_enabled_is_reported_as_reserved(tmp_path):
    base = make_world(
        tmp_path, {"systems.yaml": "systems:\n  weather: { enabled: true }\n"}
    )

    errors = validate_declarations(base)

    assert any("weather" in e and "已预留" in e for e in errors), errors


def test_memory_recent_turns_must_be_positive(tmp_path):
    base = make_world(tmp_path, {"memory.yaml": "recent_turns: 0\n"})

    errors = validate_declarations(base)

    assert any("recent_turns" in e for e in errors), errors


def test_unknown_preset(tmp_path):
    base = make_world(tmp_path, {"turn_flow.yaml": "from: chaos\nphases: []\n"})

    errors = validate_declarations(base)

    assert any("chaos" in e for e in errors), errors


def test_phase_params_must_be_declared(tmp_path):
    base = make_world(
        tmp_path,
        {"turn_flow.yaml": "phases:\n  - id: talk\n    kind: action\n    nonsense: 1\n"},
    )

    errors = validate_declarations(base)

    assert any("nonsense" in e for e in errors), errors


def test_blank_world_has_no_errors(tmp_path):
    base = tmp_path / "worlds" / "_blank"
    base.mkdir(parents=True)
    (base / "world.yaml").write_text("world_id: _blank\nname: 模板\n", encoding="utf-8")

    assert validate_declarations(base) == []


# ── C58：`win_condition`（胜负声明） ────────────────────────
#
# 判胜函数对不认识的 kind / 拼错的参数一律返回「还没结束」——不报错、不落日志，
# 表现为「游戏永不结束」。所以声明侧必须与清单（`manifest.win_kinds`）对齐。

PROFESSIONS = """
schema_version: 1
professions:
  - { id: 狼人, camp: wolf }
  - { id: 村民, camp: village }
  - { id: 预言家, camp: village }
"""

WIN_GROUP = """
win_condition:
  any_of:
    - { kind: camp_eliminated, camp: wolf }
    - { kind: group_eliminated, group: [村民], winner: wolf, label: 村民 }
"""


def _win_world(tmp_path: Path, win_block: str) -> Path:
    return make_world(
        tmp_path,
        {
            "world.yaml": "world_id: wtest\nname: 测试\n" + win_block,
            "professions.yaml": PROFESSIONS,
        },
    )


def test_win_condition_accepts_group_rule(tmp_path):
    assert validate_declarations(_win_world(tmp_path, WIN_GROUP)) == []


def test_win_condition_rejects_unknown_kind(tmp_path):
    block = "win_condition:\n  kind: moon_phase\n"

    errors = validate_declarations(_win_world(tmp_path, block))

    assert any("moon_phase" in e for e in errors), errors


def test_win_condition_requires_camp(tmp_path):
    block = "win_condition:\n  kind: camp_eliminated\n"

    errors = validate_declarations(_win_world(tmp_path, block))

    assert any("camp" in e and "缺少" in e for e in errors), errors


def test_win_condition_group_must_reference_declared_profession(tmp_path):
    block = (
        "win_condition:\n  kind: group_eliminated\n"
        "  group: [女巫]\n  winner: wolf\n"
    )

    errors = validate_declarations(_win_world(tmp_path, block))

    assert any("女巫" in e for e in errors), errors


def test_win_condition_group_must_not_be_empty(tmp_path):
    block = "win_condition:\n  kind: group_eliminated\n  group: []\n  winner: wolf\n"

    errors = validate_declarations(_win_world(tmp_path, block))

    assert any("group" in e for e in errors), errors


def test_win_condition_any_of_must_not_be_empty(tmp_path):
    block = "win_condition:\n  any_of: []\n"

    errors = validate_declarations(_win_world(tmp_path, block))

    assert any("any_of" in e for e in errors), errors


def test_win_condition_rejects_unknown_param(tmp_path):
    """参数名拼错（`groups` 少了 s）必须报错，不能被当成「没有这个参数」忽略。"""
    block = (
        "win_condition:\n  kind: group_eliminated\n"
        "  groups: [村民]\n  winner: wolf\n"
    )

    errors = validate_declarations(_win_world(tmp_path, block))

    assert any("groups" in e for e in errors), errors


def test_win_condition_winner_must_be_a_declared_camp(tmp_path):
    block = (
        "win_condition:\n  kind: group_eliminated\n"
        "  group: [村民]\n  winner: godzilla\n"
    )

    errors = validate_declarations(_win_world(tmp_path, block))

    assert any("godzilla" in e for e in errors), errors


# ── 清单只列已实现取值（不许「写了不生效」）─────────────────────

def test_action_order_rejects_unimplemented_modes(tmp_path):
    """`rotating` / `simultaneous` / `free` 未实现：要报错，不能静默退化成 dex。"""
    for mode in ("rotating", "simultaneous", "free"):
        base = make_world(
            tmp_path,
            {"turn_flow.yaml": f"phases:\n  - id: act\n    kind: action\n    order: {mode}\n"},
            world_id=f"w_{mode}",
        )
        errors = validate_declarations(base)
        assert any(mode in e for e in errors), errors


def test_broadcast_rejects_unconsumed_params(tmp_path):
    """`broadcast.audience` / `template` 没有消费方，已从清单移除。"""
    for index, key in enumerate(("audience: public", "template: 今夜平安")):
        base = make_world(
            tmp_path,
            {"turn_flow.yaml": f"phases:\n  - id: dawn\n    kind: broadcast\n    {key}\n"},
            world_id=f"b_{index}",
        )
        errors = validate_declarations(base)
        assert any("不认识参数" in e for e in errors), errors

