"""FastAPI 接口层（S1）。

设计取舍：
- 会话状态放内存（单机单人玩，不需要多实例）；落库仍然走 SQLite
- `advance` 是同步语义（一回合跑完再返回），前端用 loading 表现「GM 正在想」
- 真流式（token 级 SSE）留到 adapter 支持 `stream=True` 时再做
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..engine.loop import GameLoop, TurnRecord
from ..game import build_cards, build_llm, build_memory, build_state
from ..rules.loader import Rules
from ..models.memory import CharacterEvent
from ..storage import db
from ..storage.memory import EventStore
from ..world import WorldPackError, discover_worlds, load_world

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "trpg.db"

app = FastAPI(title="ai-trpg", version="0.1.0")
app.state.sessions = {}

# 同一时间只允许一个回合在跑（2C2G，避免并发打爆内存）
_advance_lock = asyncio.Lock()


@dataclass
class Session:
    sid: str
    pack: WorldPack
    cards: dict
    loop: GameLoop
    records: list[TurnRecord] = field(default_factory=list)
    events: Any = None
    conn: sqlite3.Connection | None = None

    @property
    def state(self):
        return self.loop.state

    def snapshot(self) -> dict[str, Any]:
        st = self.state
        return {
            "session_id": self.sid,
            "world": {"id": st.world.world_id, "name": self.pack.name, "era": st.world.era},
            "region": st.scene.region,
            "location": st.scene.location_id,
            "description": st.scene.description,
            "stage": st.stage,
            "tension": st.tension,
            "version": st.version,
            "clock": st.clock.model_dump(),
            "party": st.party.model_dump(),
            "characters": [
                {
                    "id": cid,
                    "name": self.cards[cid].data.name,
                    "profession": self.cards[cid].ext.profession,
                    "hp": rt.hp,
                    "hp_max": self.cards[cid].ext.hp_max,
                    "sanity": rt.sanity,
                    "position": rt.position.value,
                    "conditions": rt.conditions,
                }
                for cid, rt in st.characters.items()
            ],
            "turns": len(self.records),
        }


class CreateSessionReq(BaseModel):
    world: str = "ironvale"
    era: str | None = None
    players: int = Field(default=3, ge=1, le=6)
    seed: str = "42"
    provider: str = "mock"
    model: str | None = None
    reasoning_effort: str | None = None


def _new_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db.init_db(conn)
    return conn


def _get(sid: str) -> Session:
    session = app.state.sessions.get(sid)
    if session is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    return session


# ── 元信息 ──────────────────────────────────────────────────
@app.get("/api/worlds")
def list_worlds() -> dict:
    out = []
    for wid in discover_worlds(ROOT):
        try:
            pack = load_world(wid, ROOT)
        except WorldPackError:
            continue
        out.append(
            {
                "id": pack.world_id,
                "name": pack.name,
                "tone": pack.tone,
                "eras": [
                    {"id": e.id, "label": e.label, "tier_max": e.tier_max}
                    for e in pack.eras.values()
                ],
                "regions": pack.regions,
                "counts": {
                    "seeds": len(pack.seeds),
                    "items": len(pack.items),
                    "npcs": len(pack.npcs),
                },
            }
        )
    return {"worlds": out}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "sessions": len(app.state.sessions)}


# ── 会话 ────────────────────────────────────────────────────
@app.post("/api/sessions")
def create_session(req: CreateSessionReq) -> dict:
    rules = Rules(ROOT)
    try:
        pack = load_world(req.world, ROOT)
    except WorldPackError as exc:
        raise HTTPException(400, str(exc)) from exc

    cards = build_cards(req.players)
    state = build_state(pack, cards, req.era)
    conn = _new_conn()
    db.save_session(
        conn,
        state.session_id,
        req.seed,
        pack.world_id,
        state.world.era,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        name=f"{req.provider} 局",
    )
    db.save_cards(conn, state.session_id, cards)
    db.save_state(conn, state)

    store = EventStore(conn, state.session_id)
    vectors = build_memory(ROOT, req.provider, conn, state.session_id)
    llm = build_llm(
        ROOT,
        rules,
        pack,
        cards,
        req.provider,
        req.model,
        req.seed,
        req.reasoning_effort,
        events=store,
        vectors=vectors,
    )
    loop = GameLoop(
        rules, cards, state, llm, seed=req.seed, conn=conn, events=store, vectors=vectors
    )

    session = Session(
        sid=state.session_id, pack=pack, cards=cards, loop=loop, events=store, conn=conn
    )
    app.state.sessions[session.sid] = session
    return session.snapshot()


@app.get("/api/sessions")
def list_saved_sessions() -> dict:
    conn = _new_conn()
    try:
        return {"sessions": db.list_sessions(conn)}
    finally:
        conn.close()


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict:
    return _get(sid).snapshot()


@app.post("/api/sessions/{sid}/resume")
def resume_session(sid: str, provider: str = "mock") -> dict:
    """从 SQLite 恢复一个旧会话（服务重启后继续玩）。"""
    existing = app.state.sessions.get(sid)
    if existing is not None:
        return existing.snapshot()

    conn = _new_conn()
    state = db.latest_state(conn, sid)
    cards = db.load_cards(conn, sid)
    if state is None or not cards:
        conn.close()
        raise HTTPException(404, f"无法恢复会话：{sid}")

    row = conn.execute(
        "SELECT world_id, era, seed FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    world_id = row[0] if row else state.world.world_id
    seed = row[2] if row else "42"

    pack = load_world(world_id, ROOT)
    rules = Rules(ROOT)

    store = EventStore(conn, sid)
    for item in db.load_events(conn, sid):
        store.add(CharacterEvent(session_id=sid, **item))

    vectors = build_memory(ROOT, provider, conn, sid)
    llm = build_llm(
        ROOT, rules, pack, cards, provider, None, seed, None,
        events=store, vectors=vectors,
    )
    loop = GameLoop(
        rules, cards, state, llm, seed=seed, conn=conn, events=store, vectors=vectors
    )
    loop.next_turn = len(store.all_for(next(iter(cards)))) + 1

    session = Session(
        sid=sid, pack=pack, cards=cards, loop=loop, events=store, conn=conn
    )
    app.state.sessions[sid] = session
    return session.snapshot()


@app.post("/api/sessions/{sid}/advance")
async def advance(sid: str) -> dict:
    session = _get(sid)

    async with _advance_lock:
        rec = await session.loop.step()

    session.records.append(rec)
    if session.conn is not None:
        try:
            db.save_state(session.conn, session.loop.state)
        except Exception:  # noqa: BLE001
            pass
    actors = []
    for i, cid in enumerate(rec.actors):
        card = session.cards[cid]
        actors.append(
            {
                "id": cid,
                "name": card.data.name,
                "profession": card.ext.profession,
                "thought": rec.thoughts[i] if i < len(rec.thoughts) else "",
                "action": rec.actions[i] if i < len(rec.actions) else "",
                "outcome": rec.outcomes[i],
                "narration": rec.narration[i] if i < len(rec.narration) else "",
            }
        )

    return {
        "turn": rec.turn,
        "scene": rec.scene,
        "actors": actors,
        "rejected": rec.rejected,
        "elapsed_ms": rec.elapsed_ms,
        "declare_ms": rec.declare_ms,
        "state": session.snapshot(),
    }


@app.get("/api/sessions/{sid}/trace")
def get_trace(sid: str) -> dict:
    session = _get(sid)
    return {
        "session_id": sid,
        "turns": [
            {
                "turn": r.turn,
                "scene": r.scene,
                "actors": r.actors,
                "thoughts": r.thoughts,
                "actions": r.actions,
                "outcomes": r.outcomes,
                "narration": r.narration,
                "rejected": r.rejected,
                "elapsed_ms": r.elapsed_ms,
                "declare_ms": r.declare_ms,
            }
            for r in session.records
        ],
        "usage": getattr(session.loop.llm, "usage", {}),
        "memories": (
            {
                cid: [e.render() for e in session.events.all_for(cid)]
                for cid in session.cards
            }
            if session.events is not None
            else {}
        ),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    page = ROOT / "static" / "index.html"
    if not page.exists():
        return "<h1>ai-trpg</h1><p>static/index.html 不存在</p>"
    return page.read_text(encoding="utf-8")
