"""狼人杀标准 8 人板的测试夹具（搭板 + 脚本化模型，**不含任何规则**）。

被测对象永远是**真实世界包** `worlds/werewolf/` + **真实框架** `trpg/`：
`tests/test_skills.py` 那种合成包只能测框架原语，测不到世界包的声明本身
——职业牌、阵营、胜负规则、阶段参数全都只写在 `worlds/werewolf/` 里。

这里只做两件事：
1. **搭出一局标准板**（3 狼 + 预言家 + 女巫 + 猎人 + 2 村民，与
   `worlds/werewolf/professions.yaml: initial_pool` 同序）；
2. **让模型按剧本行动**（技能选择 / 投票可编排），并记录它收到过什么。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.loop import GameLoop  # noqa: E402
from trpg.models import (  # noqa: E402
    CharacterRuntime,
    GameState,
    Party,
    Scene,
    WorldRef,
)
from trpg.models.enums import Position  # noqa: E402
from trpg.rules.loader import Rules  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402
from support.builders import make_card  # noqa: E402

# 标准 8 人板：(角色 id, 职业牌, 阵营)
STANDARD_BOARD = (
    ("chr_1", "狼人", "wolf"),
    ("chr_2", "狼人", "wolf"),
    ("chr_3", "狼人", "wolf"),
    ("chr_4", "预言家", "village"),
    ("chr_5", "女巫", "village"),
    ("chr_6", "猎人", "village"),
    ("chr_7", "村民", "village"),
    ("chr_8", "村民", "village"),
)

WOLVES = ("chr_1", "chr_2", "chr_3")
GODS = ("chr_4", "chr_5", "chr_6")
VILLAGERS = ("chr_7", "chr_8")


class ScriptedLLM(MockLLM):
    """按剧本行动的 mock：技能与投票可编排，并把收到的信息记录下来。

    三个剧本入口：

    - `skill_plan[(角色, 回合)] = {"skill": 技能 id, "target": 目标 id}`
      —— 技能**不可用**时（药瓶用完 / 目标非法）剧本自动落空，不发动；
    - `vote_plan[(角色, 回合)] = [第 1 次调用, 第 2 次调用, ...]`
      —— 按**调用顺序**消费。狼每回合要投两次（夜里共议 + 白天投票），
      其余角色只投一次（白天）；用 `votes()` 构造，别手写这个列表；
    - 没写进剧本的一律**弃权**（不投票、不发动技能），避免测试之间互相污染。
    """

    def __init__(self, seed=42, skill_plan=None, vote_plan=None) -> None:
        super().__init__(seed)
        self.skill_plan = dict(skill_plan or {})
        self.vote_plan = dict(vote_plan or {})
        self._vote_cursor: dict[tuple[str, int], int] = {}
        # 记录：每个角色每回合收到的信息（briefs）
        self.briefs: dict[str, list[list[str]]] = {}
        # 记录：请求过遗言的人（谁、何时）
        self.last_words_calls: list[tuple[str, int]] = []
        # 记录：发动过的技能（谁、何时、哪个技能、打谁）
        self.skill_calls: list[tuple[str, int, str, str]] = []

    # ── 剧本驱动 ──────────────────────────────────────────────

    def skill_choice(self, actor_id, card, state, turn, skills, candidates=None):
        options = dict(candidates or {})
        plan = self.skill_plan.get((actor_id, turn))
        if not isinstance(plan, dict):
            return None
        skill_id = str(plan.get("skill") or "")
        targets = list(options.get(skill_id) or [])
        if not targets:
            # 该技能此刻不可用（药瓶用完 / 目标已死）→ 剧本落空就不发动
            return None
        target = str(plan.get("target") or "")
        if target not in targets:
            # 目标不合法（如女巫想救自己）→ 剧本落空，**不替模型换一个目标**
            return None
        self.skill_calls.append((actor_id, turn, skill_id, target))
        return {"skill": skill_id, "target": target}

    def vote(self, actor_id, card, state, turn, candidates=None):
        candidates = list(candidates or [])
        key = (actor_id, int(turn))
        index = self._vote_cursor.get(key, 0)
        self._vote_cursor[key] = index + 1
        plan = self.vote_plan.get(key)
        target = ""
        if isinstance(plan, (list, tuple)):
            if index < len(plan):
                target = str(plan[index] or "")
        elif plan:
            target = str(plan)
        # 目标必须合法（活着、且不是自己）：非法票当弃权，不替模型编票
        return target if target in candidates else None

    def last_words(self, actor_id, card, state, turn, cause="", knowledge: str = ""):
        self.last_words_calls.append((actor_id, int(turn)))
        return super().last_words(actor_id, card, state, turn, cause, knowledge)

    # ── 记录 ────────────────────────────────────────────────

    def player_action(self, actor_id, card, state, turn, previous=None):
        self.briefs.setdefault(actor_id, []).append(list(previous or []))
        return super().player_action(actor_id, card, state, turn, previous)

    def all_briefs(self, cid: str) -> list[str]:
        """某角色收到过的全部信息（跨回合摊平）。"""
        return [line for turn_briefs in self.briefs.get(cid, []) for line in turn_briefs]


def votes(turn: int, council=None, day_rounds=()) -> dict:
    """构造某回合的投票剧本。

    - `council`：狼内部共议的目标（`None` = 弃权 → 夜里没人出局）；
    - `day_rounds`：白天每一轮投票的目标，可以是统一目标，也可以是
      `{投票人: 目标}` 映射（平票 PK 的第二轮就是这么排的）。

    狼每回合的调用顺序是「共议 → 白天第 1 轮 → 白天第 2 轮」，
    其余角色只有「白天第 N 轮」——这个差异由本函数处理。
    """
    plan: dict = {}
    for cid, _profession, camp in STANDARD_BOARD:
        seq: list = []
        if camp == "wolf":
            seq.append(council)
        for round_map in day_rounds:
            if isinstance(round_map, dict):
                seq.append(round_map.get(cid))
            else:
                seq.append(round_map)
        plan[(cid, turn)] = seq
    return plan


def build_board(
    seed: int = 42,
    skill_plan=None,
    vote_plan=None,
    dead=(),
    extra=(),
    session_id: str = "ses_werewolf_test",
):
    """搭一局**标准 8 人板**（真实世界包 + 真实框架）。

    - `dead`：开局即出局的角色；
    - `extra`：额外角色 `(cid, 职业牌, 阵营)`，用于框架级互斥组之类的测试。
    """
    pack = load_world("werewolf", ROOT)
    cards: dict = {}
    characters: dict = {}
    for cid, profession, camp in list(STANDARD_BOARD) + list(extra):
        card = make_card(cid=cid, profession=profession)
        card.data.name = f"{profession}-{cid}"
        cards[cid] = card
        characters[cid] = CharacterRuntime(
            hp=10,
            sanity=10,
            camp=camp,
            position=Position.dead if cid in dead else Position.scene,
        )
    state = GameState(
        session_id=session_id,
        world=WorldRef(world_id="werewolf", era="base"),
        scene=Scene(location_id="loc_square", region="village"),
        party=Party(),
        characters=characters,
    )
    llm = ScriptedLLM(seed, skill_plan, vote_plan)
    loop = GameLoop(Rules(ROOT), cards, state, llm, seed=seed, pack=pack)
    return loop, llm


async def _drive(loop, turns: int, events: list) -> list:
    records = []
    for _ in range(turns):
        records.append(
            await loop.step(progress=lambda name, data=None: events.append((name, data)))
        )
    return records


def run_turns(loop, turns: int = 1) -> tuple[list, list]:
    """跑若干回合，返回 `(回合回执列表, 事件列表)`。"""
    events: list = []
    records = asyncio.run(_drive(loop, turns, events))
    return records, events


def events_of(events, name: str) -> list[dict]:
    """挑出某类事件（`skill` / `eulogy` / `broadcast` / `council` …）。"""
    return [data for event_name, data in events if event_name == name]


def causes(rec) -> list[str]:
    """本回合的死因列表。"""
    return [str(item.get("cause") or "") for item in rec.deaths]
