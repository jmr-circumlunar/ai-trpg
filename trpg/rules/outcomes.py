"""M02 规则 · 判定档位：**名字 + 阈值在 yaml，判定在代码**。

以前四档判定写死在 `judge.py`（`face == crit_face or margin >= crit_margin` …），
想改档位名或加一档就得改代码，前端也只能拿到 `crit_success` 这种英文枚举
（G9 痛点 6：内部枚举直接裸露给玩家）。

现在：

- `rules/ruleset.yaml: outcomes` 段声明每一档：**id / 中文 label / when**；
- `when` **只允许命名谓词**（本文件注册表），**不执行世界包表达式**
  —— 世界包不能塞代码进来，这是安全边界；
- 判定顺序 = yaml 里的顺序，第一个命中的档位生效（`otherwise` 兜底）。

doc: 03-模块/M02-规则与判定/00-模块总述.md
module: M02
layer: L1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class OutcomeError(ValueError):
    """档位表配置不合法（谓词名不存在 / 没有兜底档）。"""


@dataclass(frozen=True)
class OutcomeContext:
    """判定档位求值所需的全部输入（纯数值，不碰 state）。"""

    face: int
    margin: int
    crit_face: int
    crit_margin: int
    fail_forward_floor: int


# ── 命名谓词（`when` 只允许用这些名字）─────────────────────
# 命名风格与 `rulespack/predicates.py` 的状态谓词一致，但**不是同一套**：
# 这里的谓词看的是「一次判定的结果」，不是「世界状态」。
PREDICATES: dict[str, Callable[[OutcomeContext], bool]] = {
    # 掷出关键面，或余量足够大（老规则：face == crit or margin >= crit_margin）
    "crit_face_or_margin": lambda ctx: ctx.face == ctx.crit_face or ctx.margin >= ctx.crit_margin,
    # 余量 ≥ 0 → 成功
    "margin_at_least_zero": lambda ctx: ctx.margin >= 0,
    # 余量 ≥ 底线 → 勉强成功（失败但向前推）
    "margin_at_least_floor": lambda ctx: ctx.margin >= ctx.fail_forward_floor,
    # 兜底
    "otherwise": lambda ctx: True,
}


def predicate_names() -> list[str]:
    return sorted(PREDICATES)


def outcome_label(outcomes: list[dict[str, Any]], outcome_id: str) -> str:
    """按 id 取档位的中文 label（前端显示用；查不到返回空串，不猜）。"""
    for item in outcomes or []:
        if str(item.get("id") or "") == str(outcome_id or ""):
            return str(item.get("label") or "")
    return ""


def evaluate_outcome(outcomes: list[dict[str, Any]], ctx: OutcomeContext) -> str:
    """按 yaml 顺序求值，返回命中的档位 id。

    - 谓词名不在注册表 → 抛 `OutcomeError`（配置错误要看得见，不静默回落）；
    - 一档都没命中 → 抛错（说明表缺 `otherwise` 兜底）。
    """
    for item in outcomes or []:
        when = str(item.get("when") or "")
        fn = PREDICATES.get(when)
        if fn is None:
            raise OutcomeError(
                f"判定档位 {item.get('id')} 用了未知谓词：{when}"
                f"（可用：{', '.join(predicate_names())}）"
            )
        if fn(ctx):
            return str(item.get("id") or "")
    raise OutcomeError("判定档位表没有命中任何一档（缺 otherwise 兜底？）")


def outcome_labels(outcomes: list[dict[str, Any]]) -> dict[str, str]:
    """整张表的 id → label 映射（给前端一次拿全，省得逐个查）。"""
    return {str(item.get("id") or ""): str(item.get("label") or "") for item in outcomes or []}
