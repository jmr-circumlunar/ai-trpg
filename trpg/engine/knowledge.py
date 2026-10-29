"""M12 · 知识视图：agent 此刻「能知道什么」。

一个角色在某一刻掌握的信息，按视角裁剪后打包，注入提示词。
**没有它，模型只能在真空里决策**——这就是「agent 很蠢」的根因：
提示里没有身份、没有存活名单、没有票型、没有别人说了什么。

三条纪律（方案 38 / C67 / C68）：

1. **裁剪只用现有单源**：职业与阵营的可见性看 `secrets`，生死看 `is_alive`，
   心声看 `visibility.thoughts`，私有笔记看本人 `notes`。不发明新规则。
2. **如实**：没有的信息要写清楚「还没有」（不能留白让模型脑补出不存在的信息）。
3. **分阶段**：策略准则按**当前阶段**筛选后再注入。首夜没有发言信息，
   就不能给「谁可疑」「谁发言最少」这类准则——那会让模型在信息为零时做假推理。

**通用、零世界词**：身份名、阵营含义、行话全来自世界包（`knowledge.yaml`），
框架里一个字都不写（`check_generic` 守这条）。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: 允许进 prompt 的信息块（世界包只能用这些名字，不能自造）
SECTIONS = (
    "identity",
    "alive",
    "dead",
    "notes",
    "votes",
    "speeches",
    "stage",
    "mindset",
)

#: `when` 支持的比较形式：`cycle == 1`、`cycle >= 2`、`phase == open`
_WHEN_RE = re.compile(r"^\s*([a-z_]+)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$")


@dataclass(frozen=True)
class Knowledge:
    """一个角色此刻掌握的信息（已按视角裁剪）。"""

    enabled: bool = False
    sections: tuple[str, ...] = ()
    identity: str = ""
    alive: tuple[str, ...] = ()
    dead: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    votes: dict[str, int] = field(default_factory=dict)
    speeches: tuple[str, ...] = ()
    stage: str = ""
    strategies: tuple[str, ...] = ()
    #: 信念状态（S2-1）：角色**自己**的判断。空 = 不注入（缺省零变化）。
    mindset: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        """空知识视图 = 提示词与改造前**逐字节一致**（存量世界的零改动保证）。"""
        return not self.enabled

    def render(self) -> str:
        """拼成提示词段落；空则返回空串（调用方直接不追加）。"""
        if not self.enabled:
            return ""

        lines: list[str] = []
        if "identity" in self.sections and self.identity:
            lines.append(f"- 你的身份：{self.identity}")
        if "alive" in self.sections:
            lines.append(f"- 存活：{'、'.join(self.alive) or '（无）'}")
        if "dead" in self.sections:
            lines.append(f"- 已出局：{'、'.join(self.dead) or '（无）'}")
        if "notes" in self.sections:
            lines.append(f"- 你已确认的信息：{'；'.join(self.notes) or '（还没有）'}")
        if "votes" in self.sections and self.votes:
            tally = "、".join(f"{name} {count}票" for name, count in self.votes.items())
            lines.append(f"- 当前票型：{tally}")
        if "speeches" in self.sections:
            lines.append(f"- 你听到的公开发言：{'；'.join(self.speeches) or '（还没有人发言）'}")
        if "stage" in self.sections and self.stage:
            lines.append(f"- 当前阶段：{self.stage}")
        # 信念（S2-1）：只在角色**真的填了**时才出现；没有就一个字都不加
        if "mindset" in self.sections:
            lines.extend(self.mindset)

        parts = ["# 你现在知道的"]
        parts.extend(lines)
        if self.strategies:
            parts.append("")
            parts.append("# 此刻该守的准则")
            parts.extend(f"- {item}" for item in self.strategies)
        return "\n".join(parts)


def match_when(when: str, ctx: dict[str, Any]) -> bool:
    """判断一条策略准则此刻是否适用。

    只支持**简单比较**（`cycle == 1`、`phase == open`）——不执行任意表达式：
    世界包里的文本不能变成可执行代码。
    """
    text = str(when or "").strip()
    if not text:
        return True
    hit = _WHEN_RE.match(text)
    if not hit:
        # 写法看不懂 = 不注入（宁可不注入，也不要把看不懂的条件当成立）
        return False

    key, op, raw = hit.group(1), hit.group(2), hit.group(3).strip().strip("'\"")
    if key not in ctx:
        return False
    left = ctx[key]

    if isinstance(left, bool) or isinstance(raw, str) and not raw.isdigit():
        right: Any = raw
    elif isinstance(left, int):
        try:
            right = int(raw)
        except ValueError:
            return False
    else:
        return False

    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if not isinstance(left, int) or not isinstance(right, int):
        return False
    if op == ">=":
        return left >= right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == "<":
        return left < right
    return False


def select_strategies(
    declaration: dict[str, Any], phase: str, ctx: dict[str, Any]
) -> tuple[str, ...]:
    """按阶段与条件筛出**此刻适用**的策略准则。

    阶段过滤只看**知识阶段名**（`hidden` / `open`）：调用方（编排层）按世界包声明的
    「阶段 id → 阶段名」分组（`knowledge.yaml: phases`）映射好再传进来——
    框架因此不需要知道「夜里」是哪个阶段。
    """
    out: list[str] = []
    for item in declaration.get("strategies") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue

        # `phase` 是**知识阶段名**（hidden / open）：由调用方按世界包声明的
        # 「阶段 id → 阶段名」分组映射好再传进来。
        want_phase = str(item.get("phase") or "").strip()
        if want_phase and want_phase != phase:
            continue

        if not match_when(str(item.get("when") or ""), ctx):
            continue
        out.append(text)
    return tuple(out)


def render_mindset(mindset: Any, name_of) -> tuple[str, ...]:
    """把角色自己的**信念状态**渲染成提示词行。

    缺省为空 = 一个字都不注入（`ironvale` golden 与存量世界逐字节不变）。
    标签是**不透明标签**（世界包词表里的阵营/身份 id），框架不解释含义。
    """
    if mindset is None:
        return ()
    lines: list[str] = []

    self_belief = getattr(mindset, "self_belief", None)
    if self_belief is not None:
        pct = int(round(float(getattr(self_belief, "confidence", 0.0) or 0.0) * 100))
        lines.append(
            f"- 你认为自己是：{self_belief.label}（把握 {pct}%）"
        )

    suspicions = dict(getattr(mindset, "suspicions", None) or {})
    for target in sorted(suspicions):
        item = suspicions[target]
        label = str(getattr(item, "label", "") or "")
        pct = int(round(float(getattr(item, "confidence", 0.0) or 0.0) * 100))
        who = name_of(target) if callable(name_of) else str(target)
        tag = f"{label}，" if label else ""
        lines.append(f"- 你怀疑 {who}：{tag}{pct}% —— 依据：{item.reason}")

    return tuple(lines)


def build_knowledge(
    state: Any,
    cards: dict[str, Any],
    pack: Any,
    actor_id: str,
    *,
    phase: str = "",
    stage: str = "",
    votes: dict[str, int] | None = None,
    speeches: list[str] | None = None,
    turn: int = 0,
    #: 第几个信息隐藏周期（由编排层按世界包声明的周期长度算）
    cycle: int = 0,
) -> Knowledge:
    """构造某个角色此刻的知识视图。

    `pack` 没声明知识视图（或 `enabled: false`）→ 返回**空包**，
    调用方不往提示词里追加任何东西 → 存量世界逐字节不变。
    """
    declaration = dict(getattr(pack, "knowledge", None) or {})
    if not declaration or not declaration.get("enabled"):
        return Knowledge()

    from ..models.state import is_alive

    sections = tuple(
        item for item in (declaration.get("sections") or SECTIONS) if item in SECTIONS
    )

    def name_of(cid: str) -> str:
        card = (cards or {}).get(cid)
        return str(getattr(getattr(card, "data", None), "name", "") or cid)

    alive: list[str] = []
    dead: list[str] = []
    for cid, runtime in (getattr(state, "characters", {}) or {}).items():
        if is_alive(runtime):
            alive.append(name_of(cid))
        else:
            dead.append(name_of(cid))

    my_card = (cards or {}).get(actor_id)
    profession = str(getattr(getattr(my_card, "ext", None), "profession", "") or "")
    camp = ""
    my_runtime = (getattr(state, "characters", {}) or {}).get(actor_id)
    camp = str(getattr(my_runtime, "camp", "") or "")
    camp_labels = dict(getattr(pack, "camp_labels", {}) or {})
    identity = profession
    if profession and camp:
        identity = f"{profession}（{camp_labels.get(camp, camp)}）"

    notes = list(getattr(my_runtime, "notes", []) or [])

    ctx = {"phase": phase, "turn": int(turn or 0), "cycle": int(cycle or 0)}
    strategies = select_strategies(declaration, phase, ctx)

    return Knowledge(
        enabled=True,
        sections=sections,
        identity=identity,
        alive=tuple(alive),
        dead=tuple(dead),
        notes=tuple(notes),
        votes=dict(votes or {}),
        speeches=tuple(speeches or []),
        stage=stage,
        strategies=strategies,
        mindset=render_mindset(getattr(my_runtime, "mindset", None), name_of),
    )
