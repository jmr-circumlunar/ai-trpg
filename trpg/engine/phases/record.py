"""阶段回执的记录工具：手写路径与图路径**共用一份**。

为什么单独抽出来：`skipped` 的每一条（`id` / `kind` / `reason`）是**对外回执契约**的一部分
——`/trace`、`turn_receipts` 表、前端都读它。两条调度路径各写一遍的话，哪天加字段
（比如加 `turn` 或 `elapsed_ms`）就会漏改一处，而且不会报错，只是某条路径开始缺字段。

`reason` 必须说明原因：空的跳过记录等于埋雷——排查时只知道「它没跑」，不知道为什么。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from typing import Any


def reason_when(name: str) -> str:
    """`when` 谓词未命中。"""
    return f"when:{name}"


def reason_break(name: str) -> str:
    """`break_if` 命中后，本回合后续阶段全部跳过。"""
    return f"break_if:{name}"


def record_skip(rec: Any, spec: Any, reason: str) -> None:
    """记一次「这个阶段没跑」。"""
    rec.skipped.append({"id": spec.id, "kind": spec.kind, "reason": reason})


__all__ = ["record_skip", "reason_break", "reason_when"]
