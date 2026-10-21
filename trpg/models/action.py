from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import Intent


class PlayerAction(BaseModel):
    """玩家 Agent 的结构化输出。thought 私有，action 不含结果。"""

    schema_version: int = 1
    actor_id: str
    thought: str = ""
    action: str
    approach: str = ""
    skill: str | None = None
    target: str | None = None
    intent: Intent = Intent.normal

    @property
    def is_valid_skill(self) -> bool:
        # 真正的技能表校验在 rules.loader 里做（需要 ruleset）
        return bool(self.skill)


class JudgeResult(BaseModel):
    """代码判定的产物，LLM 只能解释它。"""

    actor_id: str
    skill: str | None
    action_type: str
    phase: str = ""
    attr: str
    attr_mod: int
    skill_bonus: int
    dc: int
    expr: str
    rolls: list[int] = Field(default_factory=list)
    total: int
    margin: int
    outcome: str
    rng_state: str = ""
