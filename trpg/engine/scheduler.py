"""M13 引擎 · 调度原语：同时行动 / free 双硬闸 / 竞价 / 意图裁决（S7-3）。

这一层是**纯逻辑**（零 LLM、可复现）：声明怎么说，引擎就怎么算。

四件事：

1. **同时行动（`active_players` 语义）**
   boardgame.io 的 `ctx.activePlayers` = **一组玩家同时行动**。
   我们的语义：`order: simultaneous` 时，一组行动者**收齐后统一结算**，
   结算顺序由 **seed 派生**的稳定排序决定（同 seed → 同结果）。
   关键不变式：**部分收齐不是合法结算点**（见 39 号方案的 checkpoint 语义）。

2. **free 模式双硬闸**：`max_rounds`（轮数）+ `token_budget`（token 预算）。
   任一触发就**确定性收束**（不再调模型）—— 这是防死循环的正确形态。

3. **发言权竞价**：分配规则由**世界包/回合流程声明**，引擎**纯代码**算分。

4. **意图动作裁决**：对话里 agent 可以提「我想做 X」，由引擎按既有合法性
   校验裁决（合法入待结算、非法拒绝并记录）—— 复用调用方传入的校验函数，
   不在这里另造规则。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..models.rng import derive

# 同时行动的声明值（与 `rulespack/manifest.py` 的 `_ORDER` 对齐）
ORDER_SIMULTANEOUS = "simultaneous"


def select_active_players(
    actors: Sequence[tuple[str, Any]],
    spec: Any = None,
    *,
    mode: str = "",
) -> list[str]:
    """同时行动的一组人（boardgame.io `activePlayers` 语义）。

    - `mode`（或阶段声明的 `order`）不是 `simultaneous` → **缺省行为不变**：
      返回全部行动者，保持原顺序；
    - 是 `simultaneous` → 返回全部行动者（这一轮**一起行动**），
      区别在于**结算前要收齐**，而不是谁先谁后。
    """
    ids = [str(cid) for cid, _ in (actors or [])]
    order = str(mode or getattr(spec, "order", "") or "")
    if order != ORDER_SIMULTANEOUS:
        return ids
    return ids


def resolve_simultaneous(
    actions: Mapping[str, Any],
    *,
    seed: Any = 0,
    turn: int = 0,
) -> list[tuple[str, Any]]:
    """**收齐之后**统一结算：按 seed 派生的稳定顺序返回动作列表。

    稳定 = 同 seed 同 turn 必得同序（可复现）；不同行动者之间没有先后含义，
    所以顺序只用于**确定性地**应用效果（避免浮点/字典序带来的漂移）。
    """
    if not actions:
        return []
    rng = derive(seed, "simultaneous", int(turn))
    items = [(str(cid), act) for cid, act in actions.items()]
    tiebreak = {cid: rng.random() for cid, _ in items}
    items.sort(key=lambda pair: (tiebreak[pair[0]], pair[0]))
    return items


def is_collected(actions: Mapping[str, Any], active_players: Sequence[str]) -> bool:
    """是否**收齐**：同时行动的所有人是否都已提交。

    部分收齐 → 不许结算（checkpoint 语义：半个行动不是合法结算点）。
    """
    if not active_players:
        return True
    have = {str(cid) for cid in actions.keys()}
    return all(str(cid) in have for cid in active_players)


@dataclass
class FreeGate:
    """free 模式（同一回合内多轮对话）的**双硬闸**。

    - `max_rounds`：轮数上限；
    - `token_budget`：token 预算上限。

    任一触发 → `should_stop()` 返回 True + 原因 → 调用方**确定性收束**
    （不再调模型）。闸的状态可序列化 → 可进 checkpoint（已用量随轮次累计）。
    """

    max_rounds: int = 0
    token_budget: int = 0
    rounds_used: int = 0
    tokens_used: int = 0
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "FreeGate":
        """从声明读双闸（缺省 = 不限；`mode` 不为 free 时也返回不限的闸）。"""
        data = dict(config or {})
        mode = str(data.get("mode") or "free")
        if mode != "free":
            return cls()
        return cls(
            max_rounds=int(data.get("max_rounds") or 0),
            token_budget=int(data.get("token_budget") or 0),
        )

    def consume_round(self) -> None:
        self.rounds_used += 1
        self._check()

    def consume_tokens(self, amount: int) -> None:
        self.tokens_used += max(0, int(amount or 0))
        self._check()

    def _check(self) -> None:
        if self.max_rounds and self.rounds_used >= self.max_rounds:
            reason = f"rounds:{self.rounds_used}/{self.max_rounds}"
            if reason not in self.reasons:
                self.reasons.append(reason)
        if self.token_budget and self.tokens_used >= self.token_budget:
            reason = f"tokens:{self.tokens_used}/{self.token_budget}"
            if reason not in self.reasons:
                self.reasons.append(reason)

    def should_stop(self) -> bool:
        """是否该收束（任一硬闸触发）。"""
        self._check()
        return bool(self.reasons)

    def stop_reason(self) -> str:
        return self.reasons[0] if self.reasons else ""

    def state(self) -> dict[str, Any]:
        """可序列化状态（进 checkpoint：已用量随轮次累计，恢复后继续扣）。"""
        return {
            "max_rounds": self.max_rounds,
            "token_budget": self.token_budget,
            "rounds_used": self.rounds_used,
            "tokens_used": self.tokens_used,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_state(cls, state: Mapping[str, Any] | None) -> "FreeGate":
        data = dict(state or {})
        return cls(
            max_rounds=int(data.get("max_rounds") or 0),
            token_budget=int(data.get("token_budget") or 0),
            rounds_used=int(data.get("rounds_used") or 0),
            tokens_used=int(data.get("tokens_used") or 0),
            reasons=[str(x) for x in (data.get("reasons") or [])],
        )


def allocate_speech_order(
    bids: Mapping[str, float],
    *,
    rule: str = "highest",
    seed: Any = 0,
    turn: int = 0,
) -> list[str]:
    """**竞价**分配发言权（谁先说更有信息量）。

    - `rule=highest`：出价高者先说；
    - `rule=lowest`：出价低者先说（反向）；
    - 同分 → 按 **seed 派生**的稳定顺序（同 seed → 同结果）。

    纯代码，零 LLM。
    """
    if not bids:
        return []
    rng = derive(seed, "bidding", int(turn))
    tiebreak = {str(cid): rng.random() for cid in bids}
    items = [(str(cid), float(value or 0.0)) for cid, value in bids.items()]
    reverse = str(rule or "highest") != "lowest"
    items.sort(key=lambda pair: (-pair[1] if reverse else pair[1], tiebreak[pair[0]], pair[0]))
    return [cid for cid, _ in items]


def judge_intent(
    intent: Any,
    validator: Callable[[Any], tuple[bool, str]] | None = None,
) -> tuple[bool, str]:
    """裁决对话里的**意图动作**（wenyoo 的 `intent`）。

    - 没有校验器 → 不接受（**不代替规则**：引擎不自己发明合法性）；
    - 校验器返回 (ok, reason) → 原样透传。

    合法 → 入待结算；非法 → 拒绝并记录原因。
    """
    if intent is None:
        return False, "没有意图"
    if validator is None:
        return False, "没有可用的合法性校验器"
    try:
        ok, reason = validator(intent)
    except Exception as exc:  # noqa: BLE001
        return False, f"校验异常：{exc}"
    return bool(ok), str(reason or "")
