"""doc: 03-模块/M01-数据模型/00-模块总述.md
module: M01
layer: L0
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .belief import Mindset
from .enums import DeltaOp, Position


class WorldRef(BaseModel):
    """注意：region 不在 world 里（region 属于 scene，随移动变化）。"""

    world_id: str
    era: str
    timeline: str = ""  # 时间点 id（如 academy_year1）；空 = 用世界默认


class Clock(BaseModel):
    """游戏内时间。只能通过 `clock.advance`（分钟数）前进，不允许倒退。

    `day` 是「第几天」的累计值，是时间推进的内核；
    `start_date` 是**开局那天的真实日期**（世界包 `calendar.start_date` 给），
    真实日期 = `start_date + (day - 1)`，是派生值——不参与推进运算，
    避免出现两套时间真相源（见方案 09）。
    """

    day: int = Field(default=1, ge=1)
    hour: int = Field(default=20, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    weather: str = "clear"
    start_date: str = ""  # YYYY-MM-DD；空 = 该世界没有日历，显示回退到「第 N 天」


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
    # 当前身份（path）：空 = 沿用角色卡上的初始身份。
    # 只能通过 StateDelta 的 characters.<id>.path 变更（剧情授予），不可凭空自造。
    path: str = ""
    # 身份授予的角色级标记（如 exec_clearance / wanted_by_school），由 grants 写入
    flags: list[str] = Field(default_factory=list)
    # 濒死起始回合（0 = 未濒死）。hp 归 0 时写入，超过宽限回合转 DEAD，救回来清零。
    dying_since: int = Field(default=0, ge=0)
    # 角色级钱与补给（P3）：每个人有自己的钱包；`party.gold/supplies` 降级为公共仓
    # （团队开销、雇人、贿赂门房这类"凑钱办事"仍走 party）
    gold: int = Field(default=0, ge=0)
    supplies: int = Field(default=0, ge=0)
    # 阵营（C56）：局设定，**不进 StateDelta 白名单**——模型不许改自己的阵营。
    # 空 = 这个角色没有阵营（传统跑团世界不受影响）。
    camp: str = ""
    # 阵营是否已公开（被公布/被验出）；未公开时不得写进别人的 prompt。
    camp_revealed: bool = False
    # 技能资源余量（A2）：`{资源 key: 还剩几次}`。空 = 不消耗资源的技能。
    # **不进 StateDelta 白名单**：药瓶数量是规则事实，模型不能写。
    skill_charges: dict[str, int] = Field(default_factory=dict)
    # 私有信息（A2）：角色自己「已经确认」的事（如查验结果、被谁救过）。
    # 只注入本人 prompt 与本人视角快照；**不进 StateDelta 白名单**。
    notes: list[str] = Field(default_factory=list)
    # 信念状态（S2-1）：自我信念 + 对他人的怀疑度（概率 + 必填依据）。
    # 缺省 None = 还没填 → 提示词与改造前逐字节一致。
    # 标签是**不透明标签**：具体阵营名来自世界包词表，框架不写死。
    mindset: Mindset | None = None


#: 即使 `hp <= 0` 仍然能行动的标记。
#:
#: 有些世界有「清零不死」的技能（不死之身 / 狂化 / 刚被复活还在硬撑），
#: 那种情况下把角色判成失能就是错的。**框架不替世界决定「归零 = 失能」**，
#: 只给默认值 + 一个豁免口子——世界包/技能往 `conditions` 里加这些标记即可。
STILL_ACTING = frozenset({"undying", "berserk", "revived"})


def is_eliminated(rt) -> bool:
    """**正式出局**：`position == dead`。这是生死的**唯一**真相。

    **HP 不是生死判据**：`hp == 0` 只是濒死（`conditions` 里加 `dying`、记 `dying_since`），
    `position` 仍是 `scene`——人还在场上，只是暂时不能动；宽限回合内可以被救回来。
    把 `hp <= 0` 当成死，会让「全员濒死但没人真正出局」的局面被误判成全灭。

    `rt` 为 None 视为已出局（保守：宁可不让他说话，也别让幽灵发言）。
    """
    if rt is None:
        return True
    position = getattr(rt, "position", None)
    return str(getattr(position, "value", position)) == "dead"


def is_alive(rt) -> bool:
    """**还在场上**（= 未出局），**与 hp 无关**。

    用在「阵营人数」「全灭判定」「阵亡名单」——濒死的队友仍然属于这个阵营，
    只是这一回合动不了。判断「能不能行动」用 `can_act()`。
    """
    return not is_eliminated(rt)


def can_act(rt) -> bool:
    """**这一回合能不能行动 / 发言 / 投票**。

    `hp <= 0` 默认不能动（濒死躺在地上），但 `STILL_ACTING` 里的标记可以豁免。
    与 `is_alive` 分开的原因：**活着 ≠ 能动**，而且「能动」的判据可能被技能改写。
    """
    if is_eliminated(rt):
        return False
    if int(getattr(rt, "hp", 1) or 0) > 0:
        return True
    conditions = set(getattr(rt, "conditions", None) or ())
    return bool(conditions & STILL_ACTING)


class Quest(BaseModel):
    id: str
    title: str
    status: Literal["active", "done", "failed"] = "active"
    progress: int = 0


class Enemy(BaseModel):
    """一场战斗里的对手。数值由代码用，叙事由 GM 写。"""

    id: str
    name: str = ""
    hp: int = Field(default=1, ge=0)
    hp_max: int = Field(default=1, ge=1)
    atk: int = 0
    def_: int = Field(default=0, alias="def")
    tags: list[str] = Field(default_factory=list)
    condition: str = ""  # 自由短标签（"受伤" / "被压制"），只用于叙事

    model_config = {"populate_by_name": True}

    @property
    def defense(self) -> int:
        return int(self.def_)


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
    # 场上敌人：hp 归 0 即从字典移除（死亡由代码判定，叙事由 GM 写）
    enemies: dict[str, Enemy] = Field(default_factory=dict)
    # 本回合每人已用发言次数（C53）。**不进 StateDelta 白名单**：只由编排层维护，
    # 但必须进状态快照（`model_dump_json` 自动带上），否则读档后配额漂移。
    comms_used: dict[str, int] = Field(default_factory=dict)
    # 胜负已定（C58）：空 = 进行中；非空 = 获胜阵营名（`all_eliminated` 时为 `all`）。
    # 由 `evaluate_win()` 的调用方写入，`win_decided` 谓词据此提前终止回合。
    winner: str = ""


class DeltaChange(BaseModel):
    path: str
    op: DeltaOp
    value: Any = None


class StateDelta(BaseModel):
    schema_version: int = 1
    turn: int = 0
    narration_meta: dict[str, Any] = Field(default_factory=dict)
    changes: list[DeltaChange] = Field(default_factory=list)
