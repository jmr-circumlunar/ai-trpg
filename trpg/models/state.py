from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .enums import DeltaOp, Position


class WorldRef(BaseModel):
    """注意：region 不在 world 里（region 属于 scene，随移动变化）。"""

    world_id: str
    era: str


class Clock(BaseModel):
    day: int = Field(default=1, ge=1)
    hour: int = Field(default=20, ge=0, le=23)
    weather: str = "clear"


class Exit(BaseModel):
    to: str
    region: str
    hint: str = ""


class Scene(BaseModel):
    location_id: str
    region: str
    description: str = ""          # 唯一自由文本槽位
    present_npcs: list[str] = Field(default_factory=list)
    exits: list[Exit] = Field(default_factory=list)


class Party(BaseModel):
    gold: int = Field(default=0, ge=0)
    supplies: int = Field(default=0, ge=0)
    flags: list[str] = Field(default_factory=list)
    reputation: dict[str, int] = Field(default_factory=dict)


class CharacterRuntime(BaseModel):
    hp: int = Field(ge=0)
    sanity: int = Field(ge=0)
    conditions: list[str] = Field(default_factory=list)
    inventory: list[str] = Field(default_factory=list)
    position: Position = Position.scene


class Quest(BaseModel):
    id: str
    title: str
    status: Literal["active", "done", "failed"] = "active"
    progress: int = 0


class GameState(BaseModel):
    """唯一真相源：只放当前事实，不放历史。"""

    schema_version: int = 1
    session_id: str
    version: int = Field(default=0, ge=0)

    world: WorldRef
    clock: Clock = Field(default_factory=Clock)
    stage: int = Field(default=1, ge=1, le=4)
    tension: int = Field(default=0, ge=0, le=100)

    scene: Scene
    party: Party = Field(default_factory=Party)
    characters: dict[str, CharacterRuntime] = Field(default_factory=dict)
    quests: list[Quest] = Field(default_factory=list)


class DeltaChange(BaseModel):
    path: str
    op: DeltaOp
    value: Any = None


class StateDelta(BaseModel):
    schema_version: int = 1
    turn: int = 0
    narration_meta: dict[str, Any] = Field(default_factory=dict)
    changes: list[DeltaChange] = Field(default_factory=list)
