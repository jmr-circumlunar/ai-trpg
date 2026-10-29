"""M13 集体决策：票型数据结构 + 纯函数计票（B55，C57）。

**投票是「阶段」而不是「工具」**：先收集所有人的票，再由代码计票，
平票按调用方传入的派生 RNG 决定——全程不调模型，同 seed 可复现。

`tally()` 刻意做成纯函数：B66 的分组共议（狼人内部先投一轮）直接复用它，
不必再写一套「谁票多」的逻辑。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

ABSTAIN = "__abstain__"


class VoteError(ValueError):
    """票型不合法（未知的 rule / tie 取值）。"""


@dataclass(frozen=True)
class VoteRecord:
    """一张票。`target=None` 是合法票（弃权），不是缺数据。"""

    voter: str
    target: str | None = None
    round: int = 1
    # 票的来源：`vote` = 正式投票；`consensus` = 组内共议（B66）。
    # 两者都进回执，但**必须能区分**——「谁投了谁」与「组内怎么商量」不是一回事。
    kind: str = "vote"
    # 投票理由（S2-4，可选）：为什么投他。空 = 旧记录（逐字节不变）。
    reason: str = ""
    # 发言里声明的怀疑对象（S2-4，可选）：用于「说的和投的」对齐检查。
    suspect: str = ""

    def as_dict(self) -> dict:
        data = {
            "voter": self.voter,
            "target": self.target,
            "round": self.round,
            "kind": self.kind,
        }
        # 只有**新字段非空**才写进去：旧记录必须逐字节不变
        if self.reason:
            data["reason"] = self.reason
        if self.suspect:
            data["suspect"] = self.suspect
        return data


def alignment(votes) -> list[dict]:
    """**发言与票的对齐检查**（S2-4）：每票给出「说的怀疑」与「投的人」。

    对齐（`aligned=True`）= 声明的怀疑对象就是投的那位；
    不对齐**不是错误**——「嘴上说怀疑 A、票投 B」本身是有价值的信号
    （可能在钓鱼，也可能临时改主意），所以只记录、不纠正。

    没有声明怀疑（`suspect` 为空）的票不进这张表。
    """
    out: list[dict] = []
    for vote in votes or []:
        suspect = str(getattr(vote, "suspect", "") or "")
        if not suspect:
            continue
        target = getattr(vote, "target", None)
        out.append(
            {
                "voter": str(getattr(vote, "voter", "") or ""),
                "declared": suspect,
                "voted": str(target or ""),
                "aligned": bool(target) and str(target) == suspect,
            }
        )
    return out


@dataclass
class VoteResult:
    """计票结果，直接写进 `TurnRecord.vote_result`。"""

    counts: dict[str, int] = field(default_factory=dict)
    winner: str | None = None
    tie: bool = False
    rounds: int = 1

    def as_dict(self) -> dict:
        return {
            "counts": dict(self.counts),
            "winner": self.winner,
            "tie": self.tie,
            "rounds": self.rounds,
        }


def tally(
    votes,
    rule: str = "plurality",
    tie: str = "random",
    rng: random.Random | None = None,
) -> VoteResult:
    """计票（纯函数）。

    | 参数 | 取值 | 语义 |
    |---|---|---|
    | `rule` | `plurality` / `majority` / `weighted` | 相对多数 / 过半 / 加权（本轮等同 plurality，权重在 B66 用） |
    | `tie` | `random` / `runoff` / `none` | 平票随机（可复现）/ 再投一轮 / 无人当选 |

    `tie=runoff` 只返回 `tie=True, winner=None`：下一轮由调用方发起，
    计票函数不自己循环（否则它就不再是纯函数了）。
    """
    if rule not in ("plurality", "majority", "weighted"):
        raise VoteError(f"未知计票规则：{rule}")
    if tie not in ("random", "runoff", "none"):
        raise VoteError(f"未知平票处理：{tie}")

    if not votes:
        return VoteResult()

    counts: dict[str, int] = {}
    for vote in votes:
        target = vote.target if hasattr(vote, "target") else vote.get("target")
        if not target:
            counts[ABSTAIN] = counts.get(ABSTAIN, 0) + 1
            continue
        counts[target] = counts.get(target, 0) + 1

    valid = {key: value for key, value in counts.items() if key != ABSTAIN}
    if not valid:
        return VoteResult(counts=counts)

    top = max(valid.values())
    leaders = sorted(key for key, value in valid.items() if value == top)

    # majority 要求严格过半，否则视为「没人赢」
    if rule == "majority" and top * 2 <= sum(valid.values()):
        leaders = []

    if len(leaders) == 1:
        return VoteResult(counts=counts, winner=leaders[0])

    if not leaders:
        return VoteResult(counts=counts, tie=True)

    if tie == "random":
        chooser = rng or random.Random(0)
        return VoteResult(counts=counts, winner=chooser.choice(leaders), tie=True)

    return VoteResult(counts=counts, winner=None, tie=True)
