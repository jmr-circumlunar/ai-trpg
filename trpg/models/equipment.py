from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .enums import Quality, SourceKind


class EquipmentSource(BaseModel):
    kind: SourceKind = SourceKind.generated
    session_id: str | None = None
    turn: int | None = None


class EquipmentInstance(BaseModel):
    """装备实例：tier 定强度档，quality 定成色，enhance 定强化。"""

    id: str
    template_id: str
    tier: int = Field(ge=1, le=5)
    quality: Quality = Quality.common
    enhance: int = Field(default=0, ge=0, le=5)
    req_level: int = Field(default=1, ge=1, le=10)
    attrs: dict[str, int] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    name_flavor: str = ""
    source: EquipmentSource = Field(default_factory=EquipmentSource)
    owner_id: str | None = None
    soulbound: bool = False

    @field_validator("tags")
    @classmethod
    def _tags_not_empty(cls, v: list[str]) -> list[str]:
        if len(v) > 3:
            raise ValueError("单件装备 tag 不得超过 3 个")
        return v

    def power(self, base: int) -> float:
        """强度 = base(tier) × quality_mult × (1 + 0.05 × enhance)"""
        return base * self.quality.mult * (1 + 0.05 * self.enhance)
