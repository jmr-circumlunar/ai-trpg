"""装配结果：`CapabilitySet` —— 一局到底开了哪些能力。

不可变（frozenset）：装配完成后不允许中途改，否则「关闭的系统其字段不可写」
这类契约会随运行状态漂移。

doc: 03-模块/M18-规则包与能力包/00-模块总述.md
module: M18
layer: L2
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .explain import describe_system


@dataclass(frozen=True)
class CapabilitySet:
    """一局的能力集合。

    `declared` 记录「哪些系统是世界包显式声明过的」，用于 explain 追源：
    同样是「开着」，显式声明和缺省全开的责任方不同。
    """

    enabled: frozenset[str]
    declared: frozenset[str] = field(default_factory=frozenset)

    def has(self, system_id: str) -> bool:
        return system_id in self.enabled

    def explains(self, system_id: str) -> str:
        return describe_system(
            system_id, enabled=self.has(system_id), declared=system_id in self.declared
        )

    # 便于调用方按习惯写 `caps.explain("combat")`
    def explain(self, system_id: str) -> str:
        return self.explains(system_id)

    def delta_owners(self) -> frozenset[str]:
        """关闭这些系统后，对应 delta 前缀应不可写。"""
        return self.enabled

    def __contains__(self, system_id: object) -> bool:  # pragma: no cover - 语法糖
        return isinstance(system_id, str) and system_id in self.enabled
