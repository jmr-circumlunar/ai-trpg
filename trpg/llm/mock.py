"""确定性 mock LLM：不需要 key，用来验证编排与可复现性。

同一 seed 下，输出的「骨架」（事件、行动、数值）完全一致；
真实模型的差异只在措辞，不在这里。
"""

from __future__ import annotations

import random

from ..models import CharacterCard, DeltaChange, GameState, PlayerAction, StateDelta
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
        self._rng = random.Random(f"{self.seed}:mock")  # 场景/叙事用（串行调用，安全）
        self._actor_rngs: dict[tuple[str, int], random.Random] = {}
        self._summaries: list[str] = []

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
            rng = random.Random(f"{self.seed}:{actor_id}:{turn}")
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
