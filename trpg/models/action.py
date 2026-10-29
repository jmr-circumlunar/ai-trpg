"""doc: 03-模块/M01-数据模型/00-模块总述.md
module: M01
layer: L0
"""
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from .enums import CombatMode, Intent


class SkillChoice(BaseModel):
    """技能选择的**统一契约**（S4-1）：人与 AI 共用同一组字段。

    M12 的 `SkillChoiceOut`（LLM 侧结构化输出）与本模型字段一致 ——
    人类提交与模型输出因此能被**同一个解析器**吃掉，不需要两套约定。
    """

    skill: str = ""
    target: str = ""


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
    # 战斗意图：attack / defend / flee；非战斗回合为 None
    mode: CombatMode | None = None

    # ── 结构化发言（S2-4）：**可选**字段，缺省空 = 旧行为逐字节不变 ──
    # 立场：我当前的判断（不透明标签，类别词表归世界包）
    stance: str = ""
    # 怀疑对象：我认为最可疑的人（角色 id）
    suspect: str = ""
    # 依据：凭什么怀疑（没有依据的怀疑不该被采信）
    evidence: str = ""
    # 欺骗类型标注（可选；类别词表归世界包，框架不写类别名）
    deception: str = ""

    # 战斗意图的容错别名：模型偶尔写中文，人类玩家更会直接写中文
    MODE_ALIAS: ClassVar[dict[str, str]] = {
        "attack": "attack",
        "攻击": "attack",
        "进攻": "attack",
        "打": "attack",
        "defend": "defend",
        "防御": "defend",
        "防守": "defend",
        "掩护": "defend",
        "flee": "flee",
        "逃跑": "flee",
        "撤退": "flee",
        "脱离": "flee",
    }

    @classmethod
    def from_raw(cls, actor_id: str, data: dict) -> PlayerAction:
        """把「LLM 输出」或「人类提交」统一解析成 PlayerAction。

        两条入口共用一份解析，避免人在中文 mode、技能名、意图上踩到和模型不同的坑。
        """
        data = data or {}

        raw_intent = str(data.get("intent") or "normal").strip().lower()
        try:
            intent = Intent(raw_intent)
        except ValueError:
            intent = Intent.normal

        raw_mode = str(data.get("mode") or "").strip().lower()
        mode = None
        key = cls.MODE_ALIAS.get(raw_mode)
        if key:
            try:
                mode = CombatMode(key)
            except ValueError:
                mode = None

        # 技能选择走**统一契约**（S4-1）：人与 AI 同一组字段、同一个解析器
        choice = SkillChoice.model_validate(
            {"skill": data.get("skill") or "", "target": data.get("target") or ""}
        )

        return cls(
            actor_id=actor_id,
            thought=str(data.get("thought") or ""),
            action=str(data.get("action") or "").strip() or "按兵不动",
            approach=str(data.get("approach") or ""),
            skill=choice.skill or None,
            target=choice.target or None,
            intent=intent,
            mode=mode,
            # 结构化发言（S2-4）：缺省空 → 旧输入逐字节不变
            stance=str(data.get("stance") or "").strip(),
            suspect=str(data.get("suspect") or "").strip(),
            evidence=str(data.get("evidence") or "").strip(),
            deception=str(data.get("deception") or "").strip(),
        )


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
