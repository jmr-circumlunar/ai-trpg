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
    thoughts: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    narration: list[str] = field(default_factory=list)
    npc_moves: list[dict] = field(default_factory=list)
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
        events=None,
        vectors=None,
    ) -> None:
        self.rules = rules
        self.cards = cards
        self.state = state
        self.llm = llm
        self.dice = DiceEngine(seed)
        self.conn = conn
        self.seed = seed
        self.next_turn = 1
        self.events = events
        self.vectors = vectors

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
        turn_day = self.state.clock.day
        turn_hour = self.state.clock.hour

        scene = await asyncio.to_thread(self.llm.describe_scene, self.state, t)
        rec = TurnRecord(turn=t, scene=scene.get("narration", ""))

        actors = self._active_actors()

        # ⚡ NPC：在场 NPC 各自按自己的目标行动（独立 Agent，并行）
        present = scene.get("present_npcs") or []
        npc_moves: list[dict] = []
        npc_hook = getattr(self.llm, "npc_action", None)
        if present and callable(npc_hook):
            npc_moves = list(
                await asyncio.gather(
                    *[
                        asyncio.to_thread(npc_hook, npc, self.state, t)
                        for npc in present
                    ]
                )
            )
            rec.npc_moves = npc_moves

        # DECLARE + RESOLVE：按 dex 顺序逐个思考并结算
        # 后者能看到前者「做了什么、结果如何」——这是跑团的核心，值得用时间换
        declare_started = time.perf_counter()
        ordered = self._order_by_dex(actors)
        actions: list = []
        results: list = []
        briefs: list[str] = []
        for cid, card in ordered:
            action = await asyncio.to_thread(
                self.llm.player_action, cid, card, self.state, t, list(briefs)
            )
            result = judge(action, card, self.state, self.rules, self.dice)
            actions.append(action)
            results.append(result)
            briefs.append(
                f"{card.name} {action.skill or '行动'}·{result.outcome}：{action.action[:30]}"
            )
        rec.declare_ms = int((time.perf_counter() - declare_started) * 1000)
        actors = ordered

        # NARRATE：合并成一次 GM 调用（串行 N 次是最大瓶颈）
        out = await asyncio.to_thread(
            self.llm.narrate_batch, self.state, actions, results, t, npc_moves
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
            fallback = self.rules.fallback_minutes([r.action_type for r in results])
            try:
                self.state = apply_delta(
                    self.state,
                    StateDelta(
                        turn=t,
                        changes=[
                            DeltaChange(
                                path="clock.advance",
                                op=DeltaOp.add,
                                value=fallback,
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
            rec.thoughts.append(action.thought)
            rec.actions.append(action.action)
            rec.outcomes.append(f"{result.outcome} ({result.total} vs {result.dc})")
            text = narrations.get(cid, "")
            rec.narration.append(text)
            self._persist(t, cid, action, result, {"narration": text}, scene)
            self._record_event(cid, action, result, t, turn_day, turn_hour)
            self._resolve_death(cid)

        self._maybe_advance_stage(t)

        # 上下文压缩：每 N 回合把旧原文压成一条回顾
        hook = getattr(self.llm, "maybe_summarize", None)
        if callable(hook):
            try:
                hook(t)
            except Exception:  # noqa: BLE001
                pass

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

    def _record_event(self, cid: str, action, result, turn: int, day: int, hour: int) -> None:
        """把本回合的经历写进该角色的记忆（结构化，供后续回忆）。"""
        if self.events is None:
            return
        from ..models.memory import IMPORTANCE_BY_OUTCOME, CharacterEvent

        skill = action.skill or "行动"
        summary = f"{skill}·{result.outcome}：{(action.action or '')[:40]}"
        entities = [self.state.scene.location_id]
        if getattr(action, "target", None):
            entities.append(str(action.target))

        self.events.add(
            CharacterEvent(
                session_id=self.state.session_id,
                char_id=cid,
                turn=turn,
                day=day,
                hour=hour,
                kind="action",
                summary=summary,
                entities=entities,
                importance=IMPORTANCE_BY_OUTCOME.get(result.outcome, 5),
            )
        )

        # 同时写入向量库：字面相似度之外，为"情境联想"留通道
        if self.vectors is not None:
            try:
                self.vectors.add_many(
                    cid,
                    [f"第{turn}回合在{self.state.scene.location_id}：{summary}"],
                    turn=turn,
                    kind="event",
                    ref_ids=[f"turn{turn}"],
                )
            except Exception:  # noqa: BLE001
                pass  # embedding 失败不影响主流程

    def _order_by_dex(self, actors: list[tuple[str, CharacterCard]]) -> list[tuple[str, CharacterCard]]:
        """先手由 dex 决定；平手按 chr_id 保证可复现。"""
        return sorted(
            actors,
            key=lambda item: (-item[1].ext.stats.get("dex", 10), item[0]),
        )

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
