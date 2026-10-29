"""从 `trpg/rulespack/manifest.py` 导出「能力清单」，给世界包作者当配置说明书。

清单是代码的投影，不是第二份真相（C59）：文档里能填的每一项，都必须是代码实际支持的。

用法：
    .\\.venv\\Scripts\\python.exe tools\\describe_capabilities.py                 # 打印
    .\\.venv\\Scripts\\python.exe tools\\describe_capabilities.py --out X.md     # 写文件
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.rulespack import manifest  # noqa: E402

STATUS_LABEL = {
    "active": "已实现",
    "planned": "已预留未实现",
    "deprecated": "已废弃",
}


def _param_default(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, dict, tuple)):
        return "`{}`".format("[]" if not value else str(list(value)))
    return f"`{value}`"


def _render_systems(lines: list[str]) -> None:
    lines += [
        "## 系统开关",
        "",
        "> 写在 `worlds/<id>/systems.yaml` 的 `systems:` 下。**不写 = 已实现系统全部开启、预留系统关闭**。",
        "",
        "| id | 状态 | 必开 | 依赖 | 影响面 | 说明 |",
        "|---|---|---|---|---|---|",
    ]
    for sid, spec in manifest.systems.items():
        deps = "、".join(spec.requires) or "—"
        conflicts = f"（冲突：{'、'.join(spec.conflicts)}）" if spec.conflicts else ""
        affects = "、".join(spec.affects) or "—"
        lines.append(
            f"| `{sid}` | {STATUS_LABEL.get(spec.status, spec.status)} | "
            f"{'是' if spec.required else '否'} | {deps} | {affects} | "
            f"{spec.summary}{conflicts} |"
        )
    lines.append("")


def _render_phase_kinds(lines: list[str]) -> None:
    lines += [
        "## 阶段种类",
        "",
        "> 写在 `worlds/<id>/turn_flow.yaml` 的 `phases[]` 里，形如 "
        "`- { id: 你起的名字, kind: 下表中的一种, ...参数 }`。",
        "> **`id` 由世界包自由取（`night`/`dawn`/`vote` 都行），`kind` 必须是下表 7 个之一**。",
        "> 昼夜两套流程 = 两个 `action` 阶段换参数，框架里没有「夜晚」这个概念。",
        "",
    ]
    for kind, spec in manifest.phase_kinds.items():
        lines += [
            f"### `{kind}` — {spec.summary}",
            "",
        ]
        if spec.requires:
            lines.append(f"所需系统：{'、'.join(spec.requires)}")
            lines.append("")
        if spec.params:
            lines += ["| 参数 | 类型 | 默认 | 说明 |", "|---|---|---|---|"]
            for name, param in spec.params.items():
                label = param.type
                if param.type == "enum":
                    label = " / ".join(f"`{v}`" for v in param.values)
                lines.append(
                    f"| `{name}` | {label} | {_param_default(param.default)} | {param.summary} |"
                )
            lines.append("")


def _render_turn_flow_defaults(lines: list[str]) -> None:
    lines += [
        "## 阶段缺省值（`declare:`）",
        "",
        "> 写在 `worlds/<id>/turn_flow.yaml` 的顶层 `declare:`，作为**所有 action 阶段的缺省参数**；",
        "> 阶段自己写了同名字段就以自己的为准。写错键或取值会被 `check_world` 拦下"
        "（以前这些键只写在注释里，写了不生效也不报错）。",
        "",
        "| 键 | 类型 | 默认 | 说明 |",
        "|---|---|---|---|",
    ]
    for name, param in manifest.turn_flow_defaults.items():
        label = " / ".join(f"`{v}`" for v in param.values) if param.values else param.type
        lines.append(
            f"| `{name}` | {label} | {_param_default(param.default)} | {param.summary} |"
        )
    lines.append("")


def _render_win_kinds(lines: list[str]) -> None:
    lines += [
        "## 胜负条件",
        "",
        "> 写在 `worlds/<id>/world.yaml` 的 `win_condition`；多条件任一命中用 `any_of:`。",
        "> **不写 = 沿用「全员出局」的旧语义**（存量世界因此零改动）。",
        "> 判据是纯函数，LLM 不参与；kind 或参数名写错会被 `check_world` 直接拦下"
        "（否则表现为「游戏永不结束」，很难查）。",
        "",
    ]
    for kind, spec in manifest.win_kinds.items():
        lines += [f"### `{kind}` — {spec.summary}", ""]
        if spec.needs_cards:
            lines.append(
                "需要角色卡（身份表）才能判；本局没有该组内身份时不判（防「空组全灭」恒真）。"
            )
            lines.append("")
        if spec.params:
            lines += ["| 参数 | 类型 | 默认 | 说明 |", "|---|---|---|---|"]
            for name, param in spec.params.items():
                lines.append(
                    f"| `{name}` | {param.type} | {_param_default(param.default)} | {param.summary} |"
                )
            lines.append("")


def _render_skills(lines: list[str]) -> None:
    lines += [
        "## 主动技能",
        "",
        "> 写在 `worlds/<id>/skills.yaml`。框架只提供**固定效果原语**"
        "（新增效果 = 机制变更），世界包只声明「谁、什么时机、什么效果、消耗什么」。",
        "> 没有这个文件 = 这个世界没有技能系统（存量世界零影响）。",
        "",
        "| 触发时机 | 说明 |",
        "|---|---|",
    ]
    for name, summary in manifest.skill_triggers.items():
        lines.append(f"| `{name}` | {summary} |")
    lines += ["", "| 效果原语 | 说明 |", "|---|---|"]
    for name, summary in manifest.skill_effects.items():
        lines.append(f"| `{name}` | {summary} |")
    lines += ["", "| 可选目标 | 说明 |", "|---|---|"]
    for name, summary in manifest.skill_targets.items():
        lines.append(f"| `{name}` | {summary} |")
    lines.append("")


def _render_predicates(lines: list[str]) -> None:
    lines += [
        "## 阶段谓词",
        "",
        "> 写在 `phases[].when` / `phases[].break_if`。谓词是纯函数 + 只读状态，"
        "不调模型、不掷骰；缺依赖系统时 `check_world` 直接报错（不许「条件永远为假」）。",
        "",
        "| 谓词 | 需要系统 | 说明 |",
        "|---|---|---|",
    ]
    for name, spec in manifest.predicates.items():
        needs = "、".join(spec.needs) or "—"
        lines.append(f"| `{name}` | {needs} | {spec.summary} |")
    lines.append("")


def _render_tools(lines: list[str]) -> None:
    lines += [
        "## Agent 工具",
        "",
        "> 工具可见性 = 下表依赖系统全部开启。关掉依赖系统，模型就看不到这个工具，"
        "prompt 里也不再列它。",
        "",
        "| 工具 | 需要系统 | 说明 |",
        "|---|---|---|",
    ]
    for name, spec in manifest.tools.items():
        needs = "、".join(spec.requires) or "—"
        lines.append(f"| `{name}` | {needs} | {spec.summary} |")
    lines.append("")


def _render_delta_fields(lines: list[str]) -> None:
    lines += [
        "## 可写状态字段",
        "",
        "> 模型能改哪些字段由装配结果决定：依赖系统关闭时该前缀不可写，写入即整包回滚。",
        "> `—` 表示不受任何系统控制（基础字段，恒可写）。",
        "",
        "| 路径前缀 | 由哪个系统提供 |",
        "|---|---|",
    ]
    for prefix, owner in manifest.delta_fields.items():
        lines.append(f"| `{prefix}` | {owner or '—'} |")
    lines.append("")


def _render_ui_blocks(lines: list[str]) -> None:
    lines += [
        "## 前端区块",
        "",
        "> 依赖系统全部开启才渲染；关闭的世界包看不到对应面板，避免「页面像坏了」。",
        "",
        "| 选择器 | 需要系统 | 说明 |",
        "|---|---|---|",
    ]
    for block in manifest.ui_blocks:
        needs = "、".join(block.requires) or "—"
        lines.append(f"| `{block.sel}` | {needs} | {block.summary} |")
    lines.append("")


def _render_world_fields(lines: list[str]) -> None:
    lines += [
        "## 世界包文件",
        "",
        "> 必填项缺失即 `check_world` 报错；可选项缺省时按默认值装配。",
        "",
        "| 文件/键 | 必填 | 默认 | 说明 |",
        "|---|---|---|---|",
    ]
    for fld in manifest.world_fields:
        default = "—" if fld.required else _param_default(fld.default)
        lines.append(
            f"| `{fld.name}` | {'是' if fld.required else '否'} | {default} | {fld.summary} |"
        )
    lines.append("")


def render_markdown() -> str:
    lines: list[str] = [
        "# 能力清单（自动生成，勿手改）",
        "",
        "> 由 `tools/describe_capabilities.py` 从 `trpg/rulespack/manifest.py` 导出。",
        "> 世界包作者按本表配置即可，不需要读代码；代码改了清单会跟着变（C59）。",
        "",
    ]
    _render_systems(lines)
    _render_phase_kinds(lines)
    _render_turn_flow_defaults(lines)
    _render_win_kinds(lines)
    _render_skills(lines)
    _render_predicates(lines)
    _render_tools(lines)
    _render_delta_fields(lines)
    _render_ui_blocks(lines)
    _render_world_fields(lines)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出能力清单")
    parser.add_argument("--out", default=None, help="写入文件；不给则打印到 stdout")
    args = parser.parse_args(argv)

    text = render_markdown()
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"已写入 {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
