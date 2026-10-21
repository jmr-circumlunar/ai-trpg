"""回合状态机（S1：玩家思考阶段并行）。

每回合的实际耗时结构：
    SCENE(1 次 LLM) + DECLARE(N 次 LLM，可并行) + NARRATE(N 次 LLM，串行)
并行后：总耗时 ≈ 1 + 1 + N 次调用，而不是 1 + N + N。

S2 会把它平移成 LangGraph 的 StateGraph——每个阶段都是一个独立方法，
迁移时基本是机械替换。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass, field

from ..models import CharacterCard, DeltaChange, GameState, StateDelta
from ..models.enums import DeltaOp, Position
from ..rules import clock as clock_mod
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
    elapsed_ms: int = 0
    declare_ms: int = 0


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
        self.next_turn = 1

    # ── 主循环 ──────────────────────────────────────────────
    async def run(self, turns: int) -> list[TurnRecord]:
        records: list[TurnRecord] = []
        for _ in range(turns):
            records.append(await self.step())
        return records

    async def step(self, turn: int | None = None) -> TurnRecord:
        """推进一个回合。API 层按需单步调用，CLI 用 run() 批量。"""
        t = turn if turn is not None else self.next_turn
        self.next_turn = t + 1
        started = time.perf_counter()

        scene = await asyncio.to_thread(self.llm.describe_scene, self.state, t)
        rec = TurnRecord(turn=t, scene=scene.get("narration", ""))

        actors = self._active_actors()

        # ⚡ DECLARE：所有玩家同时思考（最耗时的一段）
        declare_started = time.perf_counter()
        actions = await asyncio.gather(
            *[
                asyncio.to_thread(self.llm.player_action, cid, card, self.state, t)
                for cid, card in actors
            ]
        )
        rec.declare_ms = int((time.perf_counter() - declare_started) * 1000)

        # RESOLVE：串行掷骰（共享 RNG，保证可复现）
        results = [
            judge(action, card, self.state, self.rules, self.dice)
            for (cid, card), action in zip(actors, actions)
        ]

        # NARRATE：合并成一次 GM 调用（串行 N 次是最大瓶颈）
        out = await asyncio.to_thread(
            self.llm.narrate_batch, self.state, actions, results, t
        )
        narrations: dict[str, str] = out.get("narrations") or {}

        # COMMIT：整回合的 delta 一次合并
        clock_before = clock_mod.to_minutes(
            self.state.clock.day, self.state.clock.hour, self.state.clock.minute
        )
        delta = out.get("state_delta")
        if delta is not None:
            try:
                self.state = apply_delta(self.state, delta, self.cards, self.rules.clock)
            except DeltaValidationError as exc:
                rec.rejected.append("; ".join(exc.errors[:3]))

        # 时间兜底：GM 忘了申报也要往前走，否则宵禁/限时永远不会发生
        clock_after = clock_mod.to_minutes(
            self.state.clock.day, self.state.clock.hour, self.state.clock.minute
        )
        if clock_after <= clock_before:
            try:
                self.state = apply_delta(
                    self.state,
                    StateDelta(
                        turn=t,
                        changes=[
                            DeltaChange(
                                path="clock.advance",
                                op=DeltaOp.add,
                                value=self.rules.default_step_minutes,
                            )
                        ],
                    ),
                    self.cards,
                    self.rules.clock,
                )
            except DeltaValidationError as exc:
                rec.rejected.append("time: " + "; ".join(exc.errors[:2]))

        for (cid, card), action, result in zip(actors, actions, results):
            rec.actors.append(cid)
            rec.outcomes.append(f"{result.outcome} ({result.total} vs {result.dc})")
            text = narrations.get(cid, "")
            rec.narration.append(text)
            self._persist(t, cid, action, result, {"narration": text}, scene)
            self._resolve_death(cid)

        self._maybe_advance_stage(t)
        rec.version = self.state.version
        rec.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return rec

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
                self.rules.clock,
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
