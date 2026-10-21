from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .equipment import EquipmentInstance


class ModelConfig(BaseModel):
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    temperature: float = 0.9
    max_tokens: int = 800


class AIExtended(BaseModel):
    """`data.extensions.ai_trpg`：本项目扩展，绝不散落到 data 顶层。"""

    profession: str
    affiliation: str | None = None
    origin_region: str | None = None
    level: int = Field(default=1, ge=1, le=10)
    xp: int = Field(default=0, ge=0)

    stats: dict[str, int] = Field(default_factory=dict)
    hp: int = Field(ge=0)
    hp_max: int = Field(ge=1)
    sanity: int = Field(default=10, ge=0)
    sanity_max: int = Field(default=10, ge=1)

    skills: dict[str, int] = Field(default_factory=dict)

    private_goal: str = ""
    fear: str = ""
    bottom_line: str = ""

    equipment: list[EquipmentInstance] = Field(default_factory=list)
    scars: list[dict] = Field(default_factory=list)
    echo: int = 0
    wear: int = 0
    model: ModelConfig = Field(default_factory=ModelConfig)

    @field_validator("stats")
    @classmethod
    def _stats_range(cls, v: dict[str, int]) -> dict[str, int]:
        for key, val in v.items():
            if not 3 <= val <= 18:
                raise ValueError(f"属性 {key}={val} 越界（3~18）")
        return v

    @field_validator("hp")
    @classmethod
    def _hp_le_max(cls, v: int, info) -> int:
        hp_max = info.data.get("hp_max")
        if hp_max is not None and v > hp_max:
            raise ValueError("hp 不得超过 hp_max")
        return v


class CardExtensions(BaseModel):
    ai_trpg: AIExtended


class CardData(BaseModel):
    # SillyTavern V2 原生字段（照搬）
    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    tags: list[str] = Field(default_factory=list)
    character_book: dict = Field(default_factory=dict)

    extensions: CardExtensions


class CharacterCard(BaseModel):
    spec: str = "chara_card_v2"
    spec_version: str = "2.0"
    data: CardData

    @property
    def ext(self) -> AIExtended:
        return self.data.extensions.ai_trpg

    @property
    def name(self) -> str:
        return self.data.name
