"""确定性 mock LLM：不需要 key，用来验证编排与可复现性。

同一 seed 下，输出的「骨架」（事件、行动、数值）完全一致；
真实模型的差异只在措辞，不在这里。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import random

from ..models import CharacterCard, DeltaChange, GameState, PlayerAction, StateDelta
from ..models.rng import derive
from ..models.enums import DeltaOp, Intent

_SKILLS = ["撬锁", "潜行", "观察", "交涉", "攀爬"]
_SCENES = [
    "雨点敲在铁皮棚上，巷子尽头有一盏灭了的灯。",
    "空气里有潮湿的铁锈味，脚下的木板发出不该有的回响。",
    "街上没有人，但每一扇窗都开着一条缝。",
    "有人在低声数数，数到七就停了。",
]


class MockLLM:
    model = "mock"

    def __init__(self, seed: int | str) -> None:
        self.seed = str(seed)
        self._rng = derive(self.seed, "mock")  # 场景/叙事用（串行调用，安全）
        # 与 `AgentLLM` **同构**的可注入点：装配层（`Session.bind_agents` / `load_experience`）
        # 不区分 mock 与真实模型，否则接线在 mock 局里静默失效、测试根本验不到。
        self.experience: dict[str, list[str]] = {}
        self.agent_ids: dict[str, str] = {}
        self._actor_rngs: dict[tuple[str, int], random.Random] = {}
        self._summaries: list[str] = []

    def last_words(self, actor_id, card, state, turn, cause="", knowledge: str = "") -> str:
        """确定性替身：遗言按角色名派生，便于验证「出局→遗言→公布」链路。"""
        return f"我是{card.data.name}，你们会后悔的。"

    def council_say(self, actor_id, card, state, turn, transcript=None, knowledge: str = "") -> str:
        """确定性替身：按已发言条数派生一句，便于验证轮次上限与时间预算。"""
        index = len(transcript or [])
        return f"我建议先盯 index={index} 的那个，别急着动手。"

    def skill_choice(self, actor_id, card, state, turn, skills, candidates=None, knowledge: str = ""):
        """确定性替身：有技能就发动，目标取候选里 id 最小者（同 seed 必然同选）。

        真实模型会考虑局势；mock 只需要「能稳定触发四种效果」以便断言结算。
        """
        options = dict(candidates or {})
        for item in skills or []:
            # 自毁类技能（自爆那类）不在 mock 里自动发动：确定性替身不该主动送命，
            # 否则每一局 mock 都会在第一回合自爆，把别的行为断言全冲掉。
            if str(item.get("effect") or "") == "self_eliminate":
                continue
            skill_id = str(item.get("id") or "")
            targets = list(options.get(skill_id) or [])
            if targets:
                return {"skill": skill_id, "target": sorted(targets)[0]}
        return None

    def reflect(self, ending, events, existing, limit=3):
        """确定性替身：按经历条数派生一条经验，用来验证接线（真实模型会写得更像人话）。"""
        if not events:
            return []
        return [
            {
                "kind": "lesson",
                "text": f"经历了{len(events)}件事之后，我学会了先看别人怎么做再开口。",
            }
        ]

    def maybe_summarize(self, turn: int, window: int = 5) -> None:
        if window <= 0 or turn % window != 0:
            return
        self._summaries.append(f"第{turn - window + 1}~{turn}回合：队伍仍在原地打转。")
        self._summaries = self._summaries[-4:]

    def _actor_rng(self, actor_id: str, turn: int) -> random.Random:
        """每个 (actor, turn) 一个独立 RNG。

        玩家阶段是并行的，如果共用 `_rng`，线程交错会让随机序列取决于调度顺序，
        同 seed 也复现不了。按 actor 派生后，结果与并行顺序无关。
        """
        key = (actor_id, turn)
        rng = self._actor_rngs.get(key)
        if rng is None:
            rng = derive(self.seed, actor_id, turn)
            self._actor_rngs[key] = rng
        return rng

    # ── GM ──────────────────────────────────────────────────
    def describe_scene(self, state: GameState, turn: int) -> dict:
        return {
            "narration": self._rng.choice(_SCENES),
            "hooks": [self._rng.choice(["有人刚离开", "门没锁", "地上有拖痕"])],
            "present_npcs": [],
        }

    def npc_action(self, npc: dict, state: GameState, turn: int) -> dict:
        return {
            "id": str(npc.get("id") or ""),
            "name": str(npc.get("name") or ""),
            "action": "站在原地，没有动作",
            "speech": "",
        }

    def narrate_batch(
        self,
        state: GameState,
        actions: list[PlayerAction],
        results: list,
        turn: int,
        npc_moves: list[dict] | None = None,
    ) -> dict:
        narrations: dict[str, str] = {}
        changes: list[dict] = []

        for action, result in zip(actions, results):
            narrations[action.actor_id] = (
                f"{action.actor_id} 尝试{action.action}（{result.outcome}）："
                f"骰面 {result.rolls} 合计 {result.total} 对 DC {result.dc}。"
            )
            if result.outcome in ("failure", "fail_forward") and self._rng.random() < 0.6:
                changes.append(
                    {
                        "path": f"characters.{action.actor_id}.hp",
                        "op": DeltaOp.sub,
                        "value": 1,
                    }
                )
            if result.outcome == "crit_success":
                changes.append(
                    {"path": "party.flags", "op": DeltaOp.add, "value": f"clue_t{turn}"}
                )

        if self._rng.random() < 0.5:
            if state.tension > 85:
                step = -4
            elif state.tension < 20:
                step = 3
            else:
                step = self._rng.choice([-3, 2, 4])
            changes.append({"path": "tension", "op": DeltaOp.add, "value": step})

        return {
            "narrations": narrations,
            "narration_meta": {"beat": "rising" if state.tension < 60 else "climax"},
            "state_delta": StateDelta(
                turn=turn,
                changes=[DeltaChange(**c) for c in changes],
            ),
        }

    # ── PC ──────────────────────────────────────────────────
    def vote(self, actor_id, card, state, turn, candidates=None):
        """投票（B55）：同 seed + 同回合 + 同角色 → 同结果；没有候选就弃权。"""
        candidates = list(candidates or [])
        if not candidates:
            return None
        rng = self._actor_rngs.get((actor_id, turn))
        if rng is None:
            rng = derive(self.seed, actor_id, turn, "vote")
            self._actor_rngs[(actor_id, turn)] = rng
        return rng.choice(candidates)

    def player_action(
        self,
        actor_id: str,
        card: CharacterCard,
        state: GameState,
        turn: int,
        previous: list[str] | None = None,
    ) -> PlayerAction:
        rng = self._actor_rng(actor_id, turn)
        skill = rng.choice(_SKILLS)
        return PlayerAction(
            actor_id=actor_id,
            thought=f"（{card.name} 的私下盘算）先看看再说。",
            action=f"用 {skill} 试探前面的情况",
            approach="贴着墙，压低呼吸",
            skill=skill,
            intent=rng.choice([Intent.careful, Intent.normal, Intent.all_in]),
        )

    # ── 编年史 ──────────────────────────────────────────────
    def epilogue(self, state: GameState, cards: dict, kind: str = "ended", turn: int = 0) -> str:
        """离线结算：用规则版结局文本（不烧 token，也能测收尾流程）。"""
        from ..engine.ending import epilogue_text

        return epilogue_text(state, cards, kind)

    def summarize(self, state: GameState, turn: int) -> dict:
        return {
            "summaries": [
                {
                    "text": f"第 {turn} 回合，队伍在 {state.scene.location_id} 有一次交手。",
                    "entities": [state.scene.location_id],
                    "importance": 5,
                }
            ],
            "new_entities": [],
        }
