"""Agent 输出的**结构化 schema**（M2）：把「自由文本 + 手写解析」换成 schema 约束。

## 为什么是「一个联合 schema」而不是两个接口

ReAct 循环本来就是「**可能调工具，也可能直接提交行动**」——模型每次只输出其中一种。
如果拆成两个请求，循环里就要先问「你想调工具吗」再问「你要做什么」，白白多一次调用。

所以用一个 schema 把两种形态都装进来，用 `type` 区分：

```json
{"type": "tool",  "tool": "recall", "args": {"query": "铁栅"}}
{"type": "final", "thought": "…", "action": "…", "skill": "观察"}
```

好处是**循环里的 `data.get("type")` 分派一行都不用改**，而字段名与类型由 schema 保证，
少掉「模型把 `skill` 写成 `skills`」这类只有真实模型才会犯的错。

## 实测前提（见 `providers/langchain_provider.py` 文档）

MiniMax 走 `method="json_schema"` 可用，但 **prompt 里必须写明「只输出 JSON」**——
它对 `response_format` 是弱约束。现有 prompt 本来就有这句，所以不用额外加。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentTurnOut(BaseModel):
    """Agent 一次输出的联合 schema：调工具 **或** 提交行动。"""

    type: Literal["tool", "final"] = Field(
        default="final",
        description="要调工具就填 tool，要提交本回合行动就填 final",
    )

    # ── type == "tool" ────────────────────────────────────
    tool: str = Field(default="", description="工具名，只能从提示词里的工具清单选")
    args: dict = Field(default_factory=dict, description="工具参数，按工具说明填")

    # ── type == "final" ───────────────────────────────────
    thought: str = Field(default="", description="内心盘算，中文，第一人称，不超过 40 字")
    action: str = Field(default="", description="你要做什么、怎么做；不写结果，不超过 60 字")
    approach: str = Field(default="", description="方法或手段，不超过 20 字")
    skill: str | None = Field(
        default=None, description="技能名，只能从提示词给的技能表里选；不适用就留空"
    )
    target: str | None = Field(default=None, description="目标角色或物件的 id")
    intent: str = Field(default="normal", description="行动意图，默认 normal")
    mode: str | None = Field(
        default=None, description="战斗意图：attack / defend / flee；非战斗回合留空"
    )

    # ── 结构化发言（S2-4）：**可选**字段，不填就不影响旧行为 ──
    stance: str = Field(
        default="", description="你当前的立场（标签只能从提示词给的类别里选；没有就留空）"
    )
    suspect: str = Field(
        default="", description="你认为最可疑的人的角色 id；没有明确对象就留空"
    )
    evidence: str = Field(
        default="", description="凭什么怀疑：摆事实、摆行为，不要空喊；没有就留空"
    )
    deception: str = Field(
        default="", description="本回合是否在伪装（类别只能从提示词给的类别里选；没有就留空）"
    )


__all__ = ["AgentTurnOut", "MindsetOut", "SkillChoiceOut", "VoteChoiceOut"]


class SkillChoiceOut(BaseModel):
    """技能选择的输出（结构化）：`{"skill": "技能 id", "target": "角色 id"}`。

    不发动时 `skill` 填「不发动」、`target` 留空。
    用 schema 而不是自由文本：知识视图变长之后，模型更容易顺带写解释，
    自由文本解析会失败 → 技能**静默不发动**（很难查）。
    """

    skill: str = Field(default="", description="技能 id，只能从可用能力清单里选；不发动填空")
    target: str = Field(default="", description="目标角色 id 或名字；不发动填空")


class VoteChoiceOut(BaseModel):
    """投票的结构化输出（S2-4）：**投给谁 + 为什么**。

    `reason` 让票可解释；`suspect` 让「说的怀疑」与「投的人」能对齐检查
    （说怀疑 A 却投 B，是有价值的信号——不是错误）。
    """

    target: str = Field(default="", description="投给谁（角色 id）；弃权留空")
    reason: str = Field(default="", description="一句话理由，不超过 40 字；没有就留空")
    suspect: str = Field(default="", description="你发言里声明的最可疑对象 id；没有就留空")


class MindsetOut(BaseModel):
    """信念状态的结构化输出（S2-1 模型 / S2-4 schema）：自我信念 + 对他人的怀疑。

    `reason` **必填**——没有依据的怀疑度在 schema 层就被拒（C-1）。
    """

    self_belief: dict | None = Field(
        default=None, description="自我信念：{label, confidence}；label 只能从提示词给的类别里选"
    )
    suspicions: list[dict] = Field(
        default_factory=list,
        description="怀疑列表：[{target, label, confidence, reason}]；reason 必填",
    )
