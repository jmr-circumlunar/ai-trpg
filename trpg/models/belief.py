"""信念状态模型（M01 · S2-1）：**自我信念 + 对他人的怀疑度**。

为什么要有它：模型每回合都要「我认为谁可疑、我多确定、凭什么」，
没有这层结构就只能写进自由文本 → 既没法跨回合累积，也没法校准。

三条设计纪律（来自参考 C-1，抄 `LieGraph` 的表达法，不抄它的世界词）：

1. **怀疑度 = 概率 + 必填依据**：`reason` 不是可选字段，没有依据的怀疑度在
   schema 层就被拒（C-1 的核心）。
2. **框架零世界词**：这里的 `label` 是**不透明标签**，具体是什么阵营/身份
   由**世界包词表**决定（见 `belief_label_vocab`），框架里一个字都不写。
3. **信念与共享状态分离**：模型层用 Pydantic 严格校验，存进 state 时是可序列化的
   结构，跨回合累积由编排层负责（S2-2）。

缺省为空：没填信念的角色与改造前**逐字节一致**（`ironvale` golden 不变）。

doc: 03-模块/M01-数据模型/00-模块总述.md
module: M01
layer: L0
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class SelfBelief(BaseModel):
    """自我信念：模型对**自己**的判断（允许自我怀疑）。"""

    label: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("自我信念的标签不能为空")
        return text


class Suspicion(BaseModel):
    """对某个对象的怀疑度：概率 + **必填依据**。"""

    target: str = Field(min_length=1)
    label: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    #: 依据**来源**（S2-3）：先验 / 硬化 / 微调 / 跨轮平滑 —— 数字从哪来可追溯
    sources: list[str] = Field(default_factory=list)

    @field_validator("target")
    @classmethod
    def _strip_target(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("怀疑对象不能为空")
        return text

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("怀疑度必须给出依据（reason 必填）")
        return text


class Mindset(BaseModel):
    """一个角色此刻的信念集合（自我信念 + 对他人的怀疑）。"""

    self_belief: SelfBelief | None = None
    suspicions: dict[str, Suspicion] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        """空信念 = 不往提示词里注入任何东西（缺省行为零变化）。"""
        return self.self_belief is None and not self.suspicions

    def targets(self) -> tuple[str, ...]:
        """被怀疑对象的 id 列表（顺序稳定，便于快照/测试）。"""
        return tuple(sorted(self.suspicions))


def belief_label_vocab(pack: Any) -> tuple[str, ...]:
    """世界包给出的**信念标签词表**（框架零世界词）。

    词表来自世界包声明的阵营标签 id（`camp_labels` 的键）——世界包说「有哪些阵营」，
    框架只认「一组离散标签」。世界包没声明阵营时返回空表 = 不做校验
    （传统跑团世界没有阵营，也不该被逼着填阵营信念）。
    """
    labels = dict(getattr(pack, "camp_labels", None) or {})
    return tuple(sorted(str(key) for key in labels))


def check_labels(mindset: Any, vocab: tuple[str, ...] | list[str] | None) -> list[str]:
    """校验信念里的标签是否在词表内；返回错误列表（空 = 通过）。

    词表为空 = 不校验（没有词表的世界不该被拒）。
    """
    allowed = {str(x) for x in (vocab or ())}
    if not allowed:
        return []
    errors: list[str] = []
    self_belief = getattr(mindset, "self_belief", None)
    if self_belief is not None and str(self_belief.label) not in allowed:
        errors.append(f"自我信念标签不在世界包词表内：{self_belief.label}")
    for target, item in (getattr(mindset, "suspicions", None) or {}).items():
        label = str(getattr(item, "label", "") or "")
        if label and label not in allowed:
            errors.append(f"怀疑 {target} 的标签不在世界包词表内：{label}")
    return errors


__all__ = [
    "Mindset",
    "SelfBelief",
    "Suspicion",
    "belief_label_vocab",
    "check_labels",
]
