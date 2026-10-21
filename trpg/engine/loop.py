"""回合状态机（S0 手写版）。

S1 会平移成 LangGraph 的 StateGraph——因此每个阶段都是一个独立方法，
迁移时基本是机械替换。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..models import CharacterCard, DeltaChange, GameState, StateDelta
from ..models.enums import DeltaOp, Position
from ..rules.dice import DiceEngine
from ..rules.loader import Rules
from ..storage import db
from .judge import judge
from .state_merge import DeltaValidationError, apply_delta


@dataclass
class TurnRecord:
    turn: int
    scene: str
    actors: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    narration: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    version: int = 0


class GameLoop:
    def __init__(
        self,
        rules: Rules,
        cards: dict[str, CharacterCard],
        state: GameState,
        llm,
        seed: int | str = 0,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        self.rules = rules
        self.cards = cards
        self.state = state
        self.llm = llm
        self.dice = DiceEngine(seed)
        self.conn = conn
        self.seed = seed

    # ── 主循环 ──────────────────────────────────────────────
    def run(self, turns: int) -> list[TurnRecord]:
        records: list[TurnRecord] = []

        for t in range(1, turns + 1):
            scene = self.llm.describe_scene(self.state, t)
            rec = TurnRecord(turn=t, scene=scene.get("narration", ""))

            for cid, card in self._active_actors():
                action = self.llm.player_action(cid, card, self.state, t)
                result = judge(action, card, self.state, self.rules, self.dice)
                out = self.llm.narrate(self.state, action, result, t)

                rec.actors.append(cid)
                rec.outcomes.append(f"{result.outcome} ({result.total} vs {result.dc})")
                rec.narration.append(out.get("narration", ""))

                delta = out.get("state_delta")
                if delta is not None:
                    try:
                        self.state = apply_delta(self.state, delta, self.cards)
                    except DeltaValidationError as exc:
                        rec.rejected.append(f"{cid}: {'; '.join(exc.errors[:2])}")

                self._persist(t, cid, action, result, out, scene)
                self._resolve_death(cid)

            self._maybe_advance_stage(t)
            rec.version = self.state.version
            records.append(rec)

        return records

    # ── 各阶段 ──────────────────────────────────────────────
    def _active_actors(self) -> list[tuple[str, CharacterCard]]:
        result = []
        for cid, card in self.cards.items():
            rt = self.state.characters.get(cid)
            if rt is None:
                continue
            if rt.position == Position.dead or rt.hp <= 0:
                continue
            result.append((cid, card))
        return result

    def _resolve_death(self, cid: str) -> None:
        rt = self.state.characters.get(cid)
        if rt is not None and rt.hp <= 0:
            rt.position = Position.dead

    def _maybe_advance_stage(self, turn: int) -> None:
        if turn % 10 == 0 and self.state.stage < 4:
            self.state = apply_delta(
                self.state,
                StateDelta(
                    turn=turn,
                    changes=[DeltaChange(path="stage", op=DeltaOp.add, value=1)],
                ),
                self.cards,
            )

    # ── 落库 ────────────────────────────────────────────────
    def _persist(self, turn: int, cid: str, action, result, out: dict, scene: dict) -> None:
        if self.conn is None:
            return
        sid = self.state.session_id
        db.save_turn(
            self.conn,
            sid,
            turn,
            cid,
            action.thought,
            action.action,
            out.get("narration", ""),
        )
        db.save_dice(self.conn, sid, turn, result)
        db.save_agent_call(
            self.conn,
            sid,
            turn,
            "gm",
            getattr(self.llm, "model", "unknown"),
            {"scene": scene, "action": action.action},
            out,
        )
        db.save_state(self.conn, self.state)
        db.commit(self.conn)
