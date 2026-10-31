"""M16 · 可见性泄露审计（**纯函数**：不看 DB、不发请求、不含世界词）。

把「信息不泄露」从**几条手写断言**升级成**矩阵审计**：
每回合 × 每视角 × 每出口，按可见性矩阵逐项比对，输出**违规清单**（空 = 通过）。

四条规则（框架中性；矩阵由调用方给，框架只比字段与规则）：

| 规则 | 含义 |
|---|---|
| `identity_visible` | `secrets.profession: self` 时，**别人**的身份类字段必须为空 |
| `private_visible` | **别人**的私有状态（笔记 / 技能资源余量）必须为空 |
| `camp_visible` | 阵营**永不**随共享状态下发（只能走按可见性裁剪的端点） |
| `thought_visible` | 思维过程按 `visibility.thoughts` 判定（`god` 只给本人） |

**为什么要有这层**：可见性规则散在多个出口（快照 / 牌桌 / 身份端点 / 事件流），
各写各的裁剪，**改坏一个出口没有任何测试会红**。审计器把矩阵变成**可复跑的检查**。

**零世界词**：这里只处理「字段 + 规则」；阵营与身份的含义由调用方给。

doc: 03-模块/M16-可观测与评测/00-模块总述.md
module: M16
layer: L7
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

#: 身份类字段：隐藏身份时，**别人**的这些字段必须为空。
IDENTITY_FIELDS = ("profession", "path", "path_label")

#: 私有字段：只给本人与观察者（角色级「已确认的信息」）。
PRIVATE_FIELDS = ("notes", "skill_charges")

#: 阵营类字段：**永不**随共享状态下发（阵营只能走按可见性裁剪的端点）。
CAMP_FIELDS = ("camp", "camp_label")

#: 角色清单可能出现的两个键：共享快照用 `characters`，单回合结果用 `actors`。
_ROW_KEYS = ("characters", "actors")


@dataclass(frozen=True)
class Violation:
    """一条泄露违规（清单里的一条）。"""

    rule: str
    exit_name: str
    viewer: str
    target: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "exit": self.exit_name,
            "viewer": self.viewer,
            "target": self.target,
            "detail": self.detail,
        }

    def __str__(self) -> str:  # pragma: no cover - 给人看的
        return (
            f"[{self.rule}] 出口={self.exit_name} 视角={self.viewer or 'god'} "
            f"目标={self.target} {self.detail}"
        )


def audit_state_payload(
    payload: Any,
    viewer: str | None,
    *,
    secrets: dict[str, Any] | None = None,
    observer: bool = False,
    exit_name: str = "state",
) -> list[Violation]:
    """审计**共享状态类**出口（快照 / 牌桌 / 单回合结果）。

    `viewer=None` 或 `observer=True` = 观察者视角，本来就看得到全部 → 不判。
    """
    if payload is None or observer or viewer is None:
        return []
    if not isinstance(payload, dict):
        return []

    mode = str((secrets or {}).get("profession") or "all")
    out: list[Violation] = []
    for key in _ROW_KEYS:
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("id") or "")
            if not cid or cid == str(viewer):
                continue
            if mode == "self":
                for field in IDENTITY_FIELDS:
                    if field in row and str(row.get(field) or "").strip():
                        out.append(
                            Violation(
                                "identity_visible",
                                exit_name,
                                str(viewer),
                                cid,
                                f"{field}={row.get(field)!r}",
                            )
                        )
            for field in PRIVATE_FIELDS:
                if field in row and row.get(field):
                    out.append(
                        Violation(
                            "private_visible",
                            exit_name,
                            str(viewer),
                            cid,
                            f"{field}={row.get(field)!r}",
                        )
                    )
            for field in CAMP_FIELDS:
                if field in row and row.get(field) not in (None, "", False, [], {}):
                    out.append(
                        Violation(
                            "camp_visible",
                            exit_name,
                            str(viewer),
                            cid,
                            f"{field}={row.get(field)!r}",
                        )
                    )
    return out


def audit_identity_payload(
    payload: Any,
    viewer: str | None,
    *,
    secrets: dict[str, Any] | None = None,
    observer: bool = False,
    target_camp: str = "",
    viewer_camp: str = "",
    exit_name: str = "identity",
) -> list[Violation]:
    """审计**单角色身份端点**：展示出来的数据必须与矩阵一致。

    规则（与 `secrets` 矩阵同源）：

    - 职业可见 = 上帝 ∨ 本人 ∨ `secrets.profession != "self"`；
    - 阵营可见 = 上帝 ∨ 本人 ∨ (`secrets.camp == "self_and_camp"` 且同阵营)。

    `target_camp` / `viewer_camp` 由调用方给（**真相**在审计侧，不在被审载荷里）。
    """
    if not isinstance(payload, dict):
        return []
    secrets = secrets or {}
    prof_mode = str(secrets.get("profession") or "all")
    camp_mode = str(secrets.get("camp") or "self")

    cid = str(payload.get("char_id") or "")
    is_self = viewer is not None and str(viewer) == cid
    is_god = viewer is None or observer
    same_camp = bool(viewer_camp) and bool(target_camp) and viewer_camp == target_camp

    out: list[Violation] = []
    if str(payload.get("profession") or "").strip() and not (
        is_god or is_self or prof_mode != "self"
    ):
        out.append(
            Violation(
                "identity_visible",
                exit_name,
                str(viewer or ""),
                cid,
                f"profession={payload.get('profession')!r}",
            )
        )
    if str(payload.get("camp") or "").strip() and not (
        is_god or is_self or (camp_mode == "self_and_camp" and same_camp)
    ):
        out.append(
            Violation(
                "camp_visible",
                exit_name,
                str(viewer or ""),
                cid,
                f"camp={payload.get('camp')!r}",
            )
        )
    return out


def audit_log_payload(
    payload: Any,
    viewer: str | None,
    *,
    thoughts_visible: Callable[[str], bool] | None = None,
    exit_name: str = "log",
) -> list[Violation]:
    """审计**事件 / 日志流**：思维过程按可见性判定。

    `thoughts_visible(cid)` 由调用方给（复用接口层的同一个单源判定）。
    """
    if not isinstance(payload, dict) or "thought" not in payload:
        return []
    if not str(payload.get("thought") or "").strip():
        return []
    cid = str(payload.get("id") or "")
    if callable(thoughts_visible) and thoughts_visible(cid):
        return []
    return [
        Violation(
            "thought_visible",
            exit_name,
            str(viewer or ""),
            cid,
            "思维过程不应对该视角可见",
        )
    ]


def summarize(violations: list[Violation]) -> dict:
    """违规清单 → 统计（按规则计数），便于一眼看出是哪一类漏了。"""
    counts: dict[str, int] = {}
    for item in violations or []:
        counts[item.rule] = counts.get(item.rule, 0) + 1
    return {"total": len(violations or []), "by_rule": counts}


__all__ = [
    "CAMP_FIELDS",
    "IDENTITY_FIELDS",
    "PRIVATE_FIELDS",
    "Violation",
    "audit_identity_payload",
    "audit_log_payload",
    "audit_state_payload",
    "summarize",
]
