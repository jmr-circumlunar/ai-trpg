"""M18 装配声明校验：对照 `manifest.py` 检查世界包的五类声明。

为什么放在 M18 而不是 M05：判据全部来自**框架侧能力清单**（哪些系统存在、哪些阶段种类
存在、哪个谓词需要哪个系统）。M05 只负责把文件读成数据，不做语义判断。

向后兼容是硬要求：`ironvale` / `longzu` 没有任何声明文件，
`validate_declarations()` 必须返回空列表——否则新校验会把存量世界包一次打红。

对应契约：C46（声明可选，缺省走默认）、C47（依赖闭合）、C48（kind + 谓词）、
C58（胜负判法 ∈ 清单）、C59（清单单一真相）；预留系统语义见 `02-约束/06` §五。

doc: 03-模块/M18-规则包与能力包/00-模块总述.md
module: M18
layer: L2
"""

from __future__ import annotations

import re

from pathlib import Path
from typing import Any

import yaml

from .assemble import CapabilityError, assemble
from .manifest import (
    META_KEYS,
    PHASE_KINDS,
    PREDICATE_NAMES,
    SKILL_EFFECTS,
    SKILL_TARGETS,
    SKILL_TRIGGERS,
    WIN_KINDS,
    phase_kinds,
    predicates,
    systems,
    turn_flow_defaults,
    win_kinds,
)

KNOWN_PRESETS = ("standard", "minimal")

# 阶段上的「非参数」键一律用 `manifest.META_KEYS`——**不再有第二份**。
#
# 这里原先有一个 14 项的 `_PHASE_KEYS` 超集（多了 groups / visibility / order /
# tag / n / stage / quest / flag / phase）。它是「图省事」的产物：把 kind 参数也
# 当元键放行，于是校验比运行时宽松。后果实测过——给 `apply` 加 `last_words` 时
# `check_world` 通过、运行时 `parse_turn_flow` 抛错后**静默回落到默认流程**，
# 作者以为配置生效了，实际跑的是另一套。现在两边用同一份判据。


def _yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


#: 知识视图允许的信息块（与 `trpg/agents/knowledge.py::SECTIONS` 同一份口径）
KNOWLEDGE_SECTIONS = (
    "identity",
    "alive",
    "dead",
    "notes",
    "votes",
    "speeches",
    "stage",
)

#: 策略准则的条件式：`键 == 值` 这类简单比较（不执行任意表达式）
KNOWLEDGE_WHEN_RE = re.compile(r"^\s*[a-z_]+\s*(==|!=|>=|<=|>|<)\s*.+?\s*$")


def validate_declarations(base: Path) -> list[str]:
    """校验一个世界包的装配声明；返回错误列表（空 = 通过）。"""
    base = Path(base)
    errors: list[str] = []

    systems_decl = _yaml(base / "systems.yaml")
    raw_systems = systems_decl.get("systems", systems_decl) or {}
    if not isinstance(raw_systems, dict):
        return ["systems.yaml: systems 必须是 mapping"]

    world_meta = _yaml(base / "world.yaml")
    errors += _check_players(world_meta.get("players"))
    errors += _check_ui(world_meta.get("ui"), base)
    errors += _check_systems(raw_systems)
    errors += _check_turn_flow(_yaml(base / "turn_flow.yaml"), raw_systems)
    errors += _check_memory(_yaml(base / "memory.yaml"))
    errors += _check_dice(_yaml(base / "dice.yaml"))
    errors += _check_death_labels(world_meta.get("death_labels"))
    errors += _check_knowledge(_yaml(base / "knowledge.yaml"))
    professions = _profession_ids(base)
    errors += _check_win_condition(
        _yaml(base / "world.yaml").get("win_condition"),
        professions,
        _declared_camps(base),
    )
    errors += _check_skills(_yaml(base / "skills.yaml"), professions)
    return errors


# ── ① systems.yaml（C47） ───────────────────────────────────


def _check_systems(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    for system_id, cfg in raw.items():
        if system_id not in systems:
            errors.append(
                f"systems.yaml: 未知系统 {system_id}"
                f"（可用：{', '.join(sorted(systems))}）"
            )
            continue
        if cfg is not None and not isinstance(cfg, dict):
            errors.append(f"systems.yaml: {system_id} 的声明必须是 mapping")
            continue

        spec = systems[system_id]
        enabled = bool((cfg or {}).get("enabled", True))

        if enabled and spec.status == "planned":
            errors.append(
                f"systems.yaml: {system_id} 已预留未实现，"
                "不能当作已开启能力使用（约束 06 §五：要么硬失败，要么显式降级）"
            )
        if not enabled and spec.required:
            errors.append(f"systems.yaml: {system_id} 是必开系统（required），不能关闭")

        for dep in (cfg or {}).get("requires") or ():
            if dep not in systems:
                errors.append(
                    f"systems.yaml: {system_id}.requires 引用了非系统项 {dep}"
                    "（骰型/阶段序列属于装配声明，不是系统开关）"
                )

    # 依赖闭合与冲突交给装配器（同一套规则，避免两处实现漂移）
    try:
        assemble(raw)
    except CapabilityError as exc:
        errors.append(f"systems.yaml: {exc}")

    return errors


def _enabled_systems(raw: dict[str, Any]) -> set[str]:
    enabled: set[str] = set()
    for system_id, spec in systems.items():
        cfg = raw.get(system_id)
        on = bool(cfg.get("enabled", True)) if isinstance(cfg, dict) else spec.status == "active"
        if on:
            enabled.add(system_id)
    return enabled


# ── ② turn_flow.yaml（C48 + C59） ───────────────────────────


def _check_turn_flow(decl: dict[str, Any], raw_systems: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not decl:
        return errors

    preset = decl.get("from")
    if preset is not None and preset not in KNOWN_PRESETS:
        errors.append(
            f"turn_flow.yaml: from={preset} 未注册"
            f"（可用：{', '.join(KNOWN_PRESETS)}）"
        )

    errors += _check_declare(decl.get("declare"))

    phases = decl.get("phases") or []
    if not isinstance(phases, list):
        return errors + ["turn_flow.yaml: phases 必须是列表"]

    enabled = _enabled_systems(raw_systems)
    seen: set[str] = set()

    for index, phase in enumerate(phases):
        if not isinstance(phase, dict):
            errors.append(f"turn_flow.yaml: phases[{index}] 必须是 mapping")
            continue

        phase_id = str(phase.get("id") or "").strip()
        if not phase_id:
            errors.append(f"turn_flow.yaml: phases[{index}] 缺少 id")
            continue
        if phase_id in seen:
            errors.append(
                f"turn_flow.yaml: 阶段 id 重复 {phase_id}"
                "（同回合多次执行同一阶段请用 repeat）"
            )
        seen.add(phase_id)

        kind = str(phase.get("kind") or "").strip()
        if not kind:
            errors.append(
                f"turn_flow.yaml: 阶段 {phase_id} 缺少 kind"
                f"（必须 ∈ {', '.join(sorted(PHASE_KINDS))}）"
            )
            continue
        if kind not in phase_kinds:
            errors.append(
                f"turn_flow.yaml: 阶段 {phase_id} 的 kind={kind} 未注册"
                f"（可用：{', '.join(sorted(PHASE_KINDS))}）"
            )
            continue

        spec = phase_kinds[kind]
        # 与 `pipeline.parse_turn_flow` **同一份**判据（`META_KEYS`）：
        # 校验放行而运行时拒绝，会造成「不报错的静默回落」。
        allowed = set(spec.params) | META_KEYS
        for key in phase:
            if key not in allowed:
                errors.append(
                    f"turn_flow.yaml: 阶段 {phase_id}({kind}) 不认识参数 {key}"
                    f"（该 kind 可用：{', '.join(sorted(spec.params)) or '无'}）"
                )

        # 枚举取值也要校验：只查「键认不认识」不够——`order: rotating` 这种
        # 「键对、值未实现」的写法，会一路走到运行时才发现没生效。
        for key, value in phase.items():
            param = spec.params.get(key)
            if param is None or param.type != "enum":
                continue
            if value not in param.values:
                errors.append(
                    f"turn_flow.yaml: 阶段 {phase_id}({kind}) 的 {key}={value} 不在取值范围内"
                    f"（可用：{' / '.join(param.values)}）"
                )

        for dep in spec.requires:
            if dep not in enabled:
                errors.append(
                    f"turn_flow.yaml: 阶段 {phase_id}({kind}) 需要系统 {dep}，但它未开启"
                )

        # `with:` 是谓词参数（`flag` / `n` / `stage` …），必须是 mapping
        if "with" in phase and not isinstance(phase.get("with"), dict):
            errors.append(
                f"turn_flow.yaml: 阶段 {phase_id} 的 with 必须是 mapping（谓词参数）"
            )

        errors += _check_predicates(phase_id, phase, enabled)

    return errors


def _check_skills(decl: Any, professions: set[str]) -> list[str]:
    """`skills.yaml`（A2）：身份必须存在，触发时机 / 效果 / 目标 ∈ 清单。

    没有这个文件的世界（`ironvale` / `longzu`）直接跳过——技能系统是可选的。
    """
    if not decl:
        return []
    raw = decl.get("skills") if isinstance(decl, dict) else None
    if not raw:
        return []
    if not isinstance(raw, list):
        return ["skills.yaml: skills 必须是列表"]

    errors: list[str] = []
    for index, item in enumerate(raw):
        where = f"skills.yaml: skills[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where} 必须是 mapping")
            continue

        skill_id = str(item.get("id") or "").strip()
        if not skill_id:
            errors.append(f"{where} 缺少 id")
            continue
        where = f"skills.yaml: {skill_id}"

        profession = str(item.get("profession") or "").strip()
        if not profession:
            errors.append(f"{where} 缺少 profession（绑定哪个身份）")
        elif profession not in professions:
            errors.append(f"{where} 的 profession={profession} 不在 professions.yaml 里")

        trigger = str(item.get("trigger") or "turn")
        if trigger not in SKILL_TRIGGERS:
            errors.append(
                f"{where} 的 trigger={trigger} 未注册"
                f"（可用：{', '.join(sorted(SKILL_TRIGGERS))}）"
            )

        effect = str(item.get("effect") or "")
        if effect not in SKILL_EFFECTS:
            errors.append(
                f"{where} 的 effect={effect} 未注册"
                f"（可用：{', '.join(sorted(SKILL_EFFECTS))}）"
            )

        target = str(item.get("target") or "other_alive")
        if target not in SKILL_TARGETS:
            errors.append(
                f"{where} 的 target={target} 未注册"
                f"（可用：{', '.join(sorted(SKILL_TARGETS))}）"
            )

        resource = item.get("resource")
        if resource:
            if not isinstance(resource, dict):
                errors.append(f"{where} 的 resource 必须是 mapping")
            else:
                key = str(resource.get("key") or "").strip()
                if not key:
                    errors.append(f"{where} 的 resource 缺少 key")
                try:
                    charges = int(resource.get("charges", 1))
                except (TypeError, ValueError):
                    errors.append(f"{where} 的 resource.charges 必须是整数")
                else:
                    if charges < 1:
                        errors.append(f"{where} 的 resource.charges 必须 ≥ 1")

        forbidden = item.get("forbidden_if") or []
        if not isinstance(forbidden, list):
            errors.append(f"{where} 的 forbidden_if 必须是列表（死因字符串）")

    return errors


def _check_declare(decl: Any) -> list[str]:
    """`turn_flow.yaml` 顶层 `declare:`（C48）：action 阶段的缺省参数。

    与 `engine/pipeline._parse_declare` 共用 `manifest.turn_flow_defaults`。
    这条以前只写在 `action.params.order` 的注释里——世界包写了不生效、也不报错（G6-D4）。
    """
    if not decl:
        return []
    if not isinstance(decl, dict):
        return ["turn_flow.yaml: declare 必须是 mapping"]

    errors: list[str] = []
    for key, value in decl.items():
        spec = turn_flow_defaults.get(str(key))
        if spec is None:
            errors.append(
                f"turn_flow.yaml: declare 不认识键 {key}"
                f"（可用：{', '.join(sorted(turn_flow_defaults))}）"
            )
            continue
        if spec.values and value not in spec.values:
            errors.append(
                f"turn_flow.yaml: declare.{key}={value} 不在取值内"
                f"（可用：{', '.join(spec.values)}）"
            )
    return errors


def _check_predicates(
    phase_id: str, phase: dict[str, Any], enabled: set[str]
) -> list[str]:
    errors: list[str] = []
    for key in ("when", "break_if"):
        name = phase.get(key)
        if name in (None, ""):
            continue
        if name not in PREDICATE_NAMES:
            errors.append(
                f"turn_flow.yaml: 阶段 {phase_id} 的 {key}={name} 不在谓词注册表"
                f"（可用：{', '.join(sorted(PREDICATE_NAMES))}）"
            )
            continue
        missing = [dep for dep in predicates[name].needs if dep not in enabled]
        if missing:
            errors.append(
                f"turn_flow.yaml: 阶段 {phase_id} 的 {key}={name} 需要系统 "
                f"{', '.join(missing)}，但它未开启（不允许条件永远为假）"
            )
    return errors


# ── ③ memory.yaml（约束 06 §四 更正 1） ─────────────────────


def _check_memory(decl: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not decl:
        return errors

    if "recent_turns" in decl:
        try:
            recent = int(decl["recent_turns"])
        except (TypeError, ValueError):
            errors.append("memory.yaml: recent_turns 必须是整数")
        else:
            if recent < 1:
                errors.append(
                    "memory.yaml: recent_turns 必须 ≥ 1"
                    "（短期记忆不可关，只能调窗口）"
                )

    if "summary_every" in decl:
        try:
            every = int(decl["summary_every"])
        except (TypeError, ValueError):
            errors.append("memory.yaml: summary_every 必须是整数")
        else:
            if every < 0:
                errors.append("memory.yaml: summary_every 不能为负（0 = 关闭摘要）")

    return errors


# ── ④ dice.yaml（C49；骰型注册表在 B46 补齐） ───────────────


def _check_dice(decl: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not decl:
        return errors

    engine = decl.get("engine")
    if engine is not None and (not isinstance(engine, str) or not engine.strip()):
        errors.append(
            "dice.yaml: engine 必须是非空字符串（骰型注册表见 C49，B46 落地后查表校验）"
        )

    baseline = decl.get("baseline_dc")
    if baseline is not None and not isinstance(baseline, int):
        errors.append("dice.yaml: baseline_dc 必须是整数")

    return errors


# ── ⑤ world.yaml: win_condition（C58） ──────────────────────


def _profession_ids(base: Path) -> set[str]:
    doc = _yaml(base / "professions.yaml")
    return {
        str(item.get("id"))
        for item in (doc.get("professions") or [])
        if item.get("id")
    }


def _declared_camps(base: Path) -> set[str]:
    """职业牌上声明的阵营 id（世界包自己定义的词）。空集 = 这个世界不按阵营判。"""
    doc = _yaml(base / "professions.yaml")
    return {
        str(item.get("camp")) for item in (doc.get("professions") or []) if item.get("camp")
    }


def _check_win_condition(
    decl: Any, professions: set[str], camps: set[str]
) -> list[str]:
    """`world.yaml: win_condition`：kind ∈ 清单，参数 ∈ 该 kind 的声明。

    为什么必须校验：`evaluate_win` 对不认识的 kind / 拼错的参数一律返回 `None`，
    表现为「游戏永不结束」——没有报错、没有日志，只有玩到天荒地老才会发现。
    校验与实现共用 `manifest.win_kinds` 这份清单（C58 / C59）。

    没有 `win_condition` 的世界（`ironvale` / `longzu`）直接跳过：
    旧语义「全员出局」本来就无需声明。
    """
    if not decl:
        return []
    if not isinstance(decl, dict):
        return ["world.yaml: win_condition 必须是 mapping"]

    rules = decl.get("any_of")
    if rules is None:
        return _check_one_win_rule(decl, professions, camps, "win_condition")
    if not isinstance(rules, list) or not rules:
        return ["world.yaml: win_condition.any_of 必须是非空列表（空表等于永不判胜）"]

    errors: list[str] = []
    for index, item in enumerate(rules):
        where = f"win_condition.any_of[{index}]"
        if not isinstance(item, dict):
            errors.append(f"world.yaml: {where} 必须是 mapping")
            continue
        errors += _check_one_win_rule(item, professions, camps, where)
    return errors


def _check_one_win_rule(
    rule: dict[str, Any], professions: set[str], camps: set[str], where: str
) -> list[str]:
    errors: list[str] = []

    kind = str(rule.get("kind") or "").strip()
    if not kind:
        return [f"world.yaml: {where} 缺少 kind（可用：{', '.join(sorted(WIN_KINDS))}）"]
    spec = win_kinds.get(kind)
    if spec is None:
        return [
            f"world.yaml: {where} 的 kind={kind} 未注册"
            f"（可用：{', '.join(sorted(WIN_KINDS))}）"
        ]

    for key in rule:
        if key != "kind" and key not in spec.params:
            errors.append(
                f"world.yaml: {where}({kind}) 不认识参数 {key}"
                f"（该 kind 可用：{', '.join(sorted(spec.params)) or '无'}）"
            )

    for name, param in spec.params.items():
        value = rule.get(name)
        # 声明侧没有有效默认值 = 必填（与 `phase_kinds` 同一口径）
        required = param.default in (None, "", [], {})
        if value in (None, "", [], {}):
            if required:
                errors.append(
                    f"world.yaml: {where}({kind}) 缺少必填参数 {name}（{param.summary}）"
                )
            continue
        errors += _check_win_param(kind, name, value, param, professions, camps, where)

    return errors


def _check_win_param(
    kind: str,
    name: str,
    value: Any,
    param: Any,
    professions: set[str],
    camps: set[str],
    where: str,
) -> list[str]:
    errors: list[str] = []

    if param.type == "list":
        if not isinstance(value, list) or not value:
            return [f"world.yaml: {where}({kind}) 的 {name} 必须是非空列表"]
        if name == "group":
            unknown = [str(item) for item in value if str(item) not in professions]
            if unknown:
                errors.append(
                    f"world.yaml: {where}({kind}) 的 group 引用了 professions.yaml "
                    f"里不存在的身份：{sorted(unknown)}"
                )
        return errors

    if param.type == "str":
        if not isinstance(value, (str, int, float)) or not str(value).strip():
            return [f"world.yaml: {where}({kind}) 的 {name} 必须是非空字符串"]
        # 阵营词由世界包定义：世界包声明过阵营牌时才查，避免给不用阵营的世界添噪声
        if camps and name in ("camp", "winner") and str(value) not in camps:
            errors.append(
                f"world.yaml: {where}({kind}) 的 {name}={value} 不是本世界声明的阵营"
                f"（可选：{', '.join(sorted(camps))}）"
            )
    return errors


# ── ⑥ world.yaml: players（人数）与 ui（布局 + 主题） ──────────

UI_LAYOUTS = ("tabletop", "board", "flow")


def _check_players(decl) -> list[str]:
    """`players: {default, min, max}`——界面据此铺号位，不再给人数下拉。"""
    if not decl:
        return []
    if not isinstance(decl, dict):
        return ["world.yaml: players 必须是 mapping（default/min/max）"]

    errors: list[str] = []
    values = {}
    for key in ("default", "min", "max"):
        if key not in decl:
            continue
        try:
            values[key] = int(decl[key])
        except (TypeError, ValueError):
            errors.append(f"world.yaml: players.{key} 必须是整数")
    if errors:
        return errors

    low = values.get("min", 1)
    high = values.get("max", low)
    default = values.get("default", low)
    if low < 1:
        errors.append("world.yaml: players.min 必须 ≥ 1")
    if high < low:
        errors.append(f"world.yaml: players.max={high} 小于 min={low}")
    if not (low <= default <= high):
        errors.append(
            f"world.yaml: players.default={default} 不在 [{low}, {high}] 内"
        )
    return errors


def _check_ui(decl, base: Path) -> list[str]:
    """`ui: {layout, theme}`——布局只能选注册的；主题文件必须真的存在。"""
    if not decl:
        return []
    if not isinstance(decl, dict):
        return ["world.yaml: ui 必须是 mapping（layout/theme）"]

    errors: list[str] = []
    layout = str(decl.get("layout") or "")
    if layout and layout not in UI_LAYOUTS:
        errors.append(
            f"world.yaml: ui.layout={layout} 未注册（可用：{' / '.join(UI_LAYOUTS)}）"
        )

    theme = str(decl.get("theme") or "")
    if theme and not (Path(base) / "ui" / "theme.css").exists():
        # 只允许固定文件名，避免世界包用 `theme` 当任意路径去读服务器文件
        errors.append(
            "world.yaml: 声明了 ui.theme 但缺 worlds/<id>/ui/theme.css"
        )
    return errors


# ── ⑦ world.yaml: death_labels / knowledge.yaml（C67 / C68 / C71）──────────


def _check_death_labels(decl) -> list[str]:
    """`world.yaml: death_labels`——原因码 → 显示文案（世界内容）。"""
    if not decl:
        return []
    if not isinstance(decl, dict):
        return ["world.yaml: death_labels 必须是 mapping（原因码 → 显示文案）"]
    errors = []
    for key, value in decl.items():
        if not str(value or "").strip():
            errors.append(f"world.yaml: death_labels.{key} 的显示文案不能为空")
    return errors


def _check_knowledge(decl) -> list[str]:
    """`knowledge.yaml`：信息块 + 分阶段策略准则。

    校验只管**结构**（字段名、类型、可解析的条件式）；
    文案对不对是作者的事，框架不评价。
    """
    if not decl:
        return []
    if not isinstance(decl, dict):
        return ["knowledge.yaml: 必须是 mapping"]

    errors: list[str] = []
    if not isinstance(decl.get("enabled", False), bool):
        errors.append("knowledge.yaml: enabled 必须是布尔值")

    sections = decl.get("sections") or []
    if not isinstance(sections, list):
        errors.append("knowledge.yaml: sections 必须是列表")
    else:
        unknown = [str(item) for item in sections if str(item) not in KNOWLEDGE_SECTIONS]
        if unknown:
            errors.append(
                f"knowledge.yaml: sections 里有不认识的块 {sorted(unknown)}"
                f"（可用：{', '.join(KNOWLEDGE_SECTIONS)}）"
            )

    phases = decl.get("phases") or {}
    if not isinstance(phases, dict):
        errors.append("knowledge.yaml: phases 必须是 mapping（阶段名 → 阶段 id 列表）")
    else:
        for name, ids in phases.items():
            if str(name) not in ("hidden", "open"):
                errors.append(f"knowledge.yaml: phases.{name} 未注册（可用：hidden / open）")
            if not isinstance(ids, list) or not ids:
                errors.append(f"knowledge.yaml: phases.{name} 必须是非空的阶段 id 列表")

    strategies = decl.get("strategies") or []
    if not isinstance(strategies, list):
        errors.append("knowledge.yaml: strategies 必须是列表")
        return errors
    for index, item in enumerate(strategies):
        where = f"strategies[{index}]"
        if not isinstance(item, dict):
            errors.append(f"knowledge.yaml: {where} 必须是 mapping")
            continue
        if not str(item.get("id") or "").strip():
            errors.append(f"knowledge.yaml: {where} 缺少 id")
        if not str(item.get("text") or "").strip():
            errors.append(f"knowledge.yaml: {where} 缺少 text")
        phase = str(item.get("phase") or "")
        if phase and phase not in ("hidden", "open"):
            errors.append(f"knowledge.yaml: {where} 的 phase={phase} 未注册（可用：hidden / open）")
        when = str(item.get("when") or "").strip()
        if when and not KNOWLEDGE_WHEN_RE.match(when):
            errors.append(
                f"knowledge.yaml: {where} 的 when 写法看不懂（只支持 `键 == 值` 这类简单比较）"
            )
    return errors
