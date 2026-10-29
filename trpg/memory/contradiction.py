"""矛盾检测：新事实与旧记忆冲突时**标冲突，不覆盖**。

**主流做法**（联网核对 2026-10-28）：append-only 存储 + 显式冲突标记/链接，
召回方**同时看到两条并看到冲突标记**，而不是「新的把旧的盖掉」
（alphaonedev 的记忆 A2A 场景：三方都要看见 contradicts 关系；
Ryumem 用 expired_at 让旧事实退役；Skein 明确「never overwrite a contradiction」）。

**我们的判定规则**（确定性、零 LLM、零世界语义）：
1. 同 `kind`（同类型的断言）；
2. 指向同一对象：**共享实体**（`entities` 有交集）**或** 两条摘要的**最长公共前缀**
   达到阈值（中文没有空格兜底，靠 LCP 抓「同一个人/同一件事」）；
3. 两条**内容确实不同**（差异片段长度 ≥ 阈值，不是同一句话的截断版）；
4. 组内**最新回合**（同回合则重要度高者胜）为「当前版本」，
   其余条目标 `conflict=True` 并记下与谁冲突。

**不删除、不改写任何条目**——事件存储本来就是 append-only，
矛盾是「标记」，不是「覆盖」。

doc: 03-模块/M11-记忆/00-模块总述.md
module: M11
layer: L2
"""

from __future__ import annotations

from typing import Any

# 摘要差异的最小长度（字符）：低于它视为「同一句话的截断」，不算矛盾
DEFAULT_MIN_DIFF_CHARS = 2
# 摘要最长公共前缀的最小长度（字符）：中文无空格时靠它抓「同一个人」
DEFAULT_MIN_SHARED_PREFIX = 2


def shared_entities(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """两条记忆共同指向的对象。"""
    a = {str(x) for x in (left.get("entities") or []) if str(x)}
    b = {str(x) for x in (right.get("entities") or []) if str(x)}
    return sorted(a & b)


def common_prefix_len(left: str, right: str) -> int:
    """两条摘要的最长公共前缀长度。"""
    a = left or ""
    b = right or ""
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return i


def looks_like_conflict(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    min_diff_chars: int = DEFAULT_MIN_DIFF_CHARS,
    min_shared_prefix: int = DEFAULT_MIN_SHARED_PREFIX,
) -> bool:
    """判断两条记忆是否「互相矛盾」。

    `left`/`right` 至少要有 `kind` 与 `summary`（可选 `entities`）。
    """
    if str(left.get("kind") or "") != str(right.get("kind") or ""):
        return False
    a = str(left.get("summary") or "")
    b = str(right.get("summary") or "")
    if a == b:
        return False
    if len(a) - common_prefix_len(a, b) < min_diff_chars:
        return False
    if len(b) - common_prefix_len(a, b) < min_diff_chars:
        return False
    if shared_entities(left, right):
        return True
    return common_prefix_len(a, b) >= min_shared_prefix


def detect_conflicts(
    entries: list[dict[str, Any]],
    *,
    kinds: list[str] | None = None,
    min_diff_chars: int = DEFAULT_MIN_DIFF_CHARS,
    min_shared_prefix: int = DEFAULT_MIN_SHARED_PREFIX,
) -> dict[str, list[str]]:
    """找出互相矛盾的条目。

    返回 `{被标冲突的 ref: [与之冲突的 ref, ...]}`——
    **只标旧版本**（新版本不标），因为「当前版本」并不一定正确，
    标在谁身上只表示「这条与另一条不一致」，两边都会在 prompt 里出现。
    """
    out: dict[str, list[str]] = {}
    picked = [e for e in entries if not kinds or str(e.get("kind") or "") in set(kinds)]
    for i, left in enumerate(picked):
        for right in picked[i + 1 :]:
            if not looks_like_conflict(
                left,
                right,
                min_diff_chars=min_diff_chars,
                min_shared_prefix=min_shared_prefix,
            ):
                continue
            older, newer = _order(left, right)
            old_ref = str(older.get("ref") or "")
            new_ref = str(newer.get("ref") or "")
            if not old_ref or not new_ref or old_ref == new_ref:
                continue
            bucket = out.setdefault(old_ref, [])
            if new_ref not in bucket:
                bucket.append(new_ref)
    return out


def _order(left: dict[str, Any], right: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """谁更新：回合数大者新；同回合则重要度高者新。"""
    lt = int(left.get("turn") or 0)
    rt = int(right.get("turn") or 0)
    if lt != rt:
        return (left, right) if lt < rt else (right, left)
    li = float(left.get("importance") or 0.0)
    ri = float(right.get("importance") or 0.0)
    if li != ri:
        return (left, right) if li < ri else (right, left)
    return left, right


__all__ = [
    "DEFAULT_MIN_DIFF_CHARS",
    "DEFAULT_MIN_SHARED_PREFIX",
    "common_prefix_len",
    "detect_conflicts",
    "looks_like_conflict",
    "shared_entities",
]
