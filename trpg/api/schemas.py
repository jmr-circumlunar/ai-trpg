"""M14 请求体模型（只放 pydantic schema，不放逻辑）。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CharacterForm(BaseModel):
    """角色卡表单（页面编辑用，全部字段可覆盖职业默认值）。

    三层职业世界（有 `origins.yaml`）用 `origin` + `path`；旧世界用 `profession`。
    """

    name: str = Field(min_length=1, max_length=40)
    profession: str = ""
    origin: str = ""
    path: str = ""
    world: str = ""  # 空 = 用第一个可用世界（不写死世界名）
    description: str | None = None
    personality: str | None = None
    private_goal: str | None = None
    fear: str = ""
    bottom_line: str = ""
    origin_region: str | None = None
    stats: dict[str, int] | None = None
    skills: dict[str, int] | None = None
    provider: str = ""
    model: str = ""
    reasoning_effort: str = ""
    hp_max: int = Field(default=11, ge=1, le=50)
    sanity_max: int = Field(default=10, ge=1, le=50)

class CreateSessionReq(BaseModel):
    world: str = ""  # 空 = 用第一个可用世界
    era: str | None = None
    timeline: str | None = None  # 二期：开局时间点（空 = world.yaml 的 default_timeline）
    # 上限 8：信息对抗类世界（8 人板）是阶段一的验收载体，6 人开不了
    players: int = Field(default=3, ge=1, le=8)
    seed: str = "42"
    provider: str = "auto"
    model: str | None = None
    reasoning_effort: str | None = None
    style: str = ""  # 叙事风格覆盖（空 = 用世界包 world.yaml 的 style）
    character_ids: list[str] = Field(default_factory=list)
    # 二期：哪些角色由人玩（其余仍由 LLM 扮演）
    human_ids: list[str] = Field(default_factory=list)
    # S4-3：哪些**号位**（1 起）由人玩 —— 房间页只知道号位，不知道角色 id
    human_seats: list[int] = Field(default_factory=list)
    # P3：席位 → Agent（按席位顺序，空串 = 该席位没绑 Agent；一个 Agent 只能占一个席位）
    agent_ids: list[str] = Field(default_factory=list)
    inherit_from: str | None = None  # 二期：继承上一局的传闻（结局快照）

class LoginReq(BaseModel):
    username: str
    password: str

class AccountReq(BaseModel):
    username: str
    password: str
    role: str = "user"

class AgentReq(BaseModel):
    """Agent = 跨局的人格与见闻；一局里一个 Agent 占一个席位。"""

    name: str
    persona: str = ""
    voice: str = ""
    default_model_json: str = ""

class HumanActionReq(BaseModel):
    """人类玩家提交的行动：字段与 LLM 输出同构，走同一份解析。"""

    char_id: str = ""  # 空 = 提交给当前在等的那个人
    action: str = ""
    thought: str = ""
    approach: str = ""
    skill: str | None = None
    target: str | None = None
    intent: str = "normal"
    mode: str | None = None
    ghost: bool = False  # true = 本回合交还给 LLM

class SessionRenameReq(BaseModel):
    """给一局改名（存档槽的「命名存档」用）。

    名字落库（`sessions.name`），不再由浏览器本地存——换设备就丢是旧做法。
    """

    name: str = Field(min_length=1, max_length=24)




class WorldAgentsReq(BaseModel):
    """本世界参战 agent 名单（整体替换）。"""

    agent_ids: list[str] = Field(default_factory=list)


__all__ = [
    "CharacterForm",
    "CreateSessionReq",
    "LoginReq",
    "AccountReq",
    "AgentReq",
    "HumanActionReq",
    "SessionRenameReq",
    "WorldAgentsReq",
]

class EffortReq(BaseModel):
    """思考档位：standard / deep / max（由 `config/providers.yaml: efforts` 定义）。"""

    effort: str = Field(default="deep", description="思考档位名")
