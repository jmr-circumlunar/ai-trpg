"""`explain(key)`：回答「这个能力为什么是开/关的」。

排查「世界包明明写了却像没生效」时，第一句话就要能说清来源：
世界包显式声明 / 缺省已实现全开 / 已预留未实现。

doc: 03-模块/M18-规则包与能力包/00-模块总述.md
module: M18
layer: L2
"""

from __future__ import annotations

from .manifest import systems


def describe_system(system_id: str, *, enabled: bool, declared: bool) -> str:
    spec = systems.get(system_id)
    if spec is None:
        return f"{system_id} = 未知（不在系统注册表中；可用：{', '.join(sorted(systems))}）"

    if declared:
        source = "显式声明 systems.yaml"
    elif spec.status == "planned":
        source = "缺省：已预留未实现，需显式开启并接受降级"
    else:
        source = "缺省：未在 systems.yaml 声明，已实现系统默认开启"

    if enabled and spec.status == "planned":
        source += "；注意该能力尚未实现（约束 06 §五）"

    return f"{system_id} = {'开启' if enabled else '关闭'}（{source}）"


__all__ = ["describe_system"]
