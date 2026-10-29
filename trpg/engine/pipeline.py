"""M13 阶段管线：把世界包的 `turn_flow` 声明解释成「本回合要跑哪些阶段」。

这一层只做**计划**，不做执行（执行体在 `engine/phases/`）：
- 解析：`phases[]` 的 `id` + `kind` + 参数，缺省值来自 `rulespack.manifest`
- 继承：`from: standard|minimal` 只决定「未显式列出时跑什么」
- 计划：`when` 决定是否执行，`break_if` 命中后本回合后续阶段全部跳过，`repeat` 重复执行

三条不变量：
1. **`id` 由世界包自由取**，框架只认 `kind`（7 个通用类型）；
2. **计划是纯函数**：同一 state + 同一 turn → 同一计划（可复现）；
3. **跳过必须留痕**：`PhaseOutcome.skipped` 里每条都能说出原因，写进 `TurnRecord`。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..rulespack.manifest import (
    META_KEYS,
    PHASE_KINDS,
    phase_kinds,
    turn_flow_defaults,
)
from ..rulespack.predicates import PredicateContext, PredicateError, evaluate

# 框架内置流程：世界包只写「差异」，不写全量
PRESETS: dict[str, tuple[dict[str, Any], ...]] = {
    "standard": (
        {"id": "scene", "kind": "narration"},
        {"id": "declare", "kind": "action", "order": "dex", "visibility": "previous"},
        {"id": "resolve", "kind": "action", "judge": True},
        {"id": "narrate", "kind": "broadcast"},
        {"id": "commit", "kind": "commit"},
    ),
    "minimal": (
        {"id": "scene", "kind": "narration"},
        {"id": "declare", "kind": "action"},
        {"id": "narrate", "kind": "broadcast"},
    ),
}

# 阶段对象上不属于某个 kind 参数的键（由管线自己解释）
# 元键只有一份：`rulespack.manifest.META_KEYS`。校验器与解析器共用，
# 避免「校验放行、运行时拒绝 → 静默回落默认流程」。
_META_KEYS = META_KEYS


class PipelineError(ValueError):
    """`turn_flow` 声明不合法（kind 未注册 / 参数不认识 / repeat 非正）。"""


@dataclass(frozen=True)
class PhaseSpec:
    """一个待执行的阶段：世界包的名字 + 框架的 kind + 该 kind 的参数。"""

    id: str
    kind: str
    when: str = "always"
    break_if: str = ""
    repeat: int = 1
    params: Mapping[str, Any] = field(default_factory=dict)
    # 谓词参数（`when` / `break_if` 用的），与 kind 参数分开：见 `manifest.META_KEYS`
    with_params: Mapping[str, Any] = field(default_factory=dict)
    # 主持人播报台词（世界包写，框架原样发事件；空 = 不播报）
    banner: str = ""

    def param(self, name: str, default: Any = None) -> Any:
        return self.params.get(name, default)


@dataclass
class PhaseOutcome:
    """本回合的执行计划结果，直接对应 `TurnRecord.order` / `skipped`。"""

    executed: list[str] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    broken: str | None = None

    @property
    def order(self) -> list[str]:
        """C54：实际执行顺序（所有 kind 都记录）。"""
        return list(self.executed)


def parse_turn_flow(declaration: Mapping[str, Any] | None) -> tuple[PhaseSpec, ...]:
    """把 `turn_flow.yaml` 的声明解析成阶段序列；空声明 = `standard` preset。

    顶层 `declare:` 是 **action 阶段的缺省参数**（顺序 / 可见性）：只写一次，
    各个 action 阶段各取所需，阶段自己写了就以自己的为准。
    """
    decl = dict(declaration or {})
    preset = str(decl.get("from") or "standard")
    if preset not in PRESETS:
        raise PipelineError(
            f"未注册的 from={preset}（可用：{', '.join(sorted(PRESETS))}）"
        )

    defaults = _parse_declare(decl.get("declare"))

    raw_phases = decl.get("phases") or list(PRESETS[preset])
    if not isinstance(raw_phases, Sequence) or isinstance(raw_phases, (str, bytes)):
        raise PipelineError("turn_flow: phases 必须是列表")

    return tuple(_parse_phase(item, defaults) for item in raw_phases)


def _parse_declare(raw: Any) -> dict[str, Any]:
    """解析顶层 `declare:`（D4）：未知键 / 非法取值一律报错，不许静默忽略。

    与 `validate.py` 共用 `manifest.turn_flow_defaults` 这一份清单。
    """
    if not raw:
        return {}
    if not isinstance(raw, Mapping):
        raise PipelineError("turn_flow: declare 必须是 mapping")

    out: dict[str, Any] = {}
    for key, value in raw.items():
        spec = turn_flow_defaults.get(str(key))
        if spec is None:
            raise PipelineError(
                f"turn_flow: declare 不认识键 {key}"
                f"（可用：{', '.join(sorted(turn_flow_defaults))}）"
            )
        if spec.values and value not in spec.values:
            raise PipelineError(
                f"turn_flow: declare.{key}={value} 不在取值内"
                f"（可用：{', '.join(spec.values)}）"
            )
        out[str(key)] = value
    return out


def _parse_phase(item: Any, defaults: Mapping[str, Any] | None = None) -> PhaseSpec:
    if not isinstance(item, Mapping):
        raise PipelineError(f"turn_flow: 阶段必须是 mapping，收到 {type(item).__name__}")

    phase_id = str(item.get("id") or "").strip()
    if not phase_id:
        raise PipelineError("turn_flow: 阶段缺少 id")

    kind = str(item.get("kind") or "").strip()
    if not kind:
        raise PipelineError(
            f"turn_flow: 阶段 {phase_id} 缺少 kind（必须 ∈ {', '.join(sorted(PHASE_KINDS))}）"
        )
    if kind not in phase_kinds:
        raise PipelineError(
            f"turn_flow: 阶段 {phase_id} 的 kind={kind} 未注册"
            f"（可用：{', '.join(sorted(PHASE_KINDS))}）"
        )

    spec = phase_kinds[kind]
    params: dict[str, Any] = {name: p.default for name, p in spec.params.items()}
    # `declare:` 只给 action 阶段补缺省值：阶段自己写了同名字段就不覆盖。
    if kind == "action" and defaults:
        for key, value in defaults.items():
            if key in params and key not in item:
                params[key] = value
    for key, value in item.items():
        if key in _META_KEYS:
            continue
        if key not in spec.params:
            raise PipelineError(
                f"turn_flow: 阶段 {phase_id}({kind}) 不认识参数 {key}"
                f"（该 kind 可用：{', '.join(sorted(spec.params)) or '无'}）"
            )
        params[key] = value

    raw_repeat = item.get("repeat", 1)
    try:
        repeat = int(raw_repeat)
    except (TypeError, ValueError) as exc:
        raise PipelineError(
            f"turn_flow: 阶段 {phase_id} 的 repeat 必须是整数"
        ) from exc
    if repeat < 1:
        raise PipelineError(f"turn_flow: 阶段 {phase_id} 的 repeat 必须 ≥ 1")

    raw_with = item.get("with") or {}
    if not isinstance(raw_with, Mapping):
        raise PipelineError(
            f"turn_flow: 阶段 {phase_id} 的 with 必须是 mapping（谓词参数）"
        )

    return PhaseSpec(
        id=phase_id,
        kind=kind,
        when=str(item.get("when") or "always"),
        break_if=str(item.get("break_if") or ""),
        repeat=repeat,
        params=params,
        with_params=dict(raw_with),
        banner=str(item.get("banner") or ""),
    )


def plan_turn(
    phases: Sequence[PhaseSpec], ctx: PredicateContext
) -> PhaseOutcome:
    """按 `when` / `break_if` / `repeat` 决定本回合执行哪些阶段。

    `break_if` 在**该阶段执行之后**求值：语义是「这一步做完发现该结束了」。
    """
    outcome = PhaseOutcome()

    for spec in phases:
        for _ in range(spec.repeat):
            if outcome.broken is not None:
                outcome.skipped.append(
                    {
                        "id": spec.id,
                        "kind": spec.kind,
                        "reason": f"break_if:{outcome.broken}",
                    }
                )
                continue

            if not _check(spec.when, ctx):
                outcome.skipped.append(
                    {"id": spec.id, "kind": spec.kind, "reason": f"when:{spec.when}"}
                )
                continue

            outcome.executed.append(spec.id)

            if spec.break_if and _check(spec.break_if, ctx):
                outcome.broken = spec.break_if

    return outcome


def _check(name: str, ctx: PredicateContext) -> bool:
    try:
        return evaluate(name, ctx)
    except PredicateError as exc:
        raise PipelineError(f"阶段条件求值失败：{exc}") from exc
