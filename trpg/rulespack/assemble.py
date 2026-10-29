"""M18 装配器：把世界包的能力声明变成 `CapabilitySet`。

缺省语义（B39 的硬要求）：世界包不声明 `systems` 时，
**已实现系统全部开启、预留系统关闭**——即 `ironvale` / `longzu` 今天没写任何声明文件，
行为必须与改动前完全一致。

依赖闭合（C47）在这里一次做完，`check_world` 只负责把错误提前到开局之前。

doc: 03-模块/M18-规则包与能力包/00-模块总述.md
module: M18
layer: L2
"""

from __future__ import annotations

from typing import Any, Mapping

from .manifest import systems
from .systems import CapabilitySet


class CapabilityError(ValueError):
    """世界包的能力声明不合法（未知系统 / 依赖未开 / 必开项被关 / 语义冲突）。"""


def assemble(source: Any = None) -> CapabilitySet:
    """装配能力集合。

    source 三种形态：
    - `None` 或 `dict`：直接给 `systems` 声明；
    - `WorldPack`：调它的 `systems_declaration()`，并把结果缓存回 pack（同一局只装配一次）。
    """
    if source is None or isinstance(source, Mapping):
        declaration = dict(source or {})
        caps = _build(declaration)
        return caps

    cached = getattr(source, "_caps_cache", None)
    if cached is not None:
        return cached

    declaration = dict(source.systems_declaration() or {})
    caps = _build(declaration)
    setattr(source, "_caps_cache", caps)
    return caps


def _build(declaration: Mapping[str, Any]) -> CapabilitySet:
    declared: dict[str, bool] = {}
    extra_requires: dict[str, tuple[str, ...]] = {}

    for system_id, raw in declaration.items():
        if system_id not in systems:
            raise CapabilityError(
                f"未知系统：{system_id}（可用：{', '.join(sorted(systems))}）"
            )
        cfg: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        if raw is not None and not isinstance(raw, Mapping):
            raise CapabilityError(
                f"系统 {system_id} 的声明必须是 mapping，收到 {type(raw).__name__}"
            )
        declared[system_id] = bool(cfg.get("enabled", True))
        requires = tuple(cfg.get("requires") or ())
        for dep in requires:
            if dep not in systems:
                raise CapabilityError(
                    f"{system_id}.requires 引用了非系统项 {dep}："
                    "骰型/阶段序列属于装配声明，不是系统开关（C47）"
                )
        if requires:
            extra_requires[system_id] = requires

    enabled = {
        system_id
        for system_id, spec in systems.items()
        if declared.get(system_id, spec.status == "active")
    }

    for system_id, on in declared.items():
        if not on and systems[system_id].required:
            raise CapabilityError(
                f"{system_id} 是必开系统（required），不能关闭（C47）"
            )

    for system_id in sorted(enabled):
        deps = tuple(systems[system_id].requires) + extra_requires.get(system_id, ())
        for dep in deps:
            if dep not in enabled:
                raise CapabilityError(
                    f"{system_id} requires {dep}，但 {dep} 未开启（依赖闭合 C47）"
                )

    for system_id in sorted(enabled):
        for other in systems[system_id].conflicts:
            if other in enabled:
                raise CapabilityError(
                    f"{system_id} 与 {other} 语义冲突，不能同开（C47）"
                )

    return CapabilitySet(enabled=frozenset(enabled), declared=frozenset(declared))


def tool_set(caps: CapabilitySet, comms_mode: str = "") -> frozenset[str]:
    """按能力过滤后的工具集合（C51）：依赖系统全开，且 `requires_comms` 的工具还要有交流。

    `comms_mode` 是世界的交流档位（`turn_flow.yaml: comms.mode`）。
    这条判据以前**只**写在两处 `visible_tools` 的 `if name == "say"` 里，
    `tool_set` 自己不知道，于是在没有交流的世界里仍然返回 `say`——
    同一份「能力」在不同调用点给出不同答案，拿它做权限校验就会漏。
    """
    from .manifest import tools as tool_specs

    mode = str(comms_mode or "none")
    out: set[str] = set()
    for name, spec in tool_specs.items():
        if not all(caps.has(dep) for dep in spec.requires):
            continue
        if spec.requires_comms and mode == "none":
            continue
        out.add(name)
    return frozenset(out)
