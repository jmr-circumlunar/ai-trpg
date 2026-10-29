"""M14 共享上下文：app 实例、会话缓存、鉴权、公共 helper。

- 本模块是 api 层的「单一共享状态」，routes/* 一律 `from ..deps import *` 取用。
- 故意全量导出（含 _ 前缀的 helper）：这是同一层内的共享上下文，不是公共 API。
- 路由分组见 routes/，请求体见 schemas.py，装配见 app.py。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..engine import ending as ending_mod
from ..engine.human import HumanSeat
from ..growth import carry as meta_carry
from ..growth import earn as meta_earn
from ..growth import shop as meta_shop
from ..growth.hooks import load_hooks
from ..growth.wallet import Wallet
from ..engine.loop import GameLoop, TurnRecord
from ..game import (
    build_cards,
    build_director,
    build_llm,
    build_memory,
    build_state,
    hydrate_llm,
    seed_lore,
)
from ..rules.loader import Rules
from ..growth.build import card_from_form, preset_cards
from ..models.memory import CharacterEvent
from ..models.state import is_eliminated
from ..storage import db
from ..storage import auth as auth_mod
from ..storage import ledger as world_ledger
from ..storage.characters import CharacterRepo
from ..storage.memory import EventStore
from ..world import WorldPackError, discover_worlds, load_world
from ..ops import health_snapshot

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "trpg.db"

# ── 认证（公网开放用）────────────────────────────────────────
# 放行路径：登录页、登录接口、探活。其余一律要登录。
PUBLIC_PATHS = {"/login", "/api/login", "/api/health", "/favicon.ico"}
SESSION_DAYS = int(os.getenv("TRPG_SESSION_DAYS", "7"))
SECURE_COOKIE = str(os.getenv("TRPG_SECURE_COOKIE", "")).lower() in ("1", "true", "yes")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """启动时确保有管理员账号（公网开放的前提）。"""
    _ensure_admin_account()
    yield


app = FastAPI(title="ai-trpg", version="0.1.0", lifespan=_lifespan)
app.state.sessions = {}
# 认证开关：默认开启（公网开放的前提）。
# 测试里由 conftest 的 autouse fixture 关掉，认证本身由 tests/test_auth.py 单独测。
app.state.auth_required = str(os.getenv("TRPG_AUTH_REQUIRED", "1")).lower() not in (
    "0",
    "false",
    "no",
)


def _ensure_admin_account() -> None:
    """第一次启动时建管理员：优先用环境变量，否则生成随机密码打印一次。"""
    conn = _new_conn()
    try:
        if db.count_accounts(conn) > 0:
            return
        username = os.getenv("TRPG_ADMIN_USER", "admin").strip() or "admin"
        password = os.getenv("TRPG_ADMIN_PASSWORD", "")
        generated = not password
        if not password:
            password = secrets.token_urlsafe(12)
        db.create_account(
            conn, username=username, password_hash=auth_mod.hash_password(password), role="admin"
        )
        print(
            f"[auth] 已创建管理员账号 {username}｜密码：{password}"
            + ("（随机生成，请立即登录后修改）" if generated else ""),
            flush=True,
        )
    finally:
        conn.close()


def _current_account(request) -> dict | None:
    """从 cookie 解析登录账号；无效/过期/停用 → None。"""
    token = request.cookies.get(auth_mod.COOKIE_NAME)
    if not token:
        return None
    conn = _new_conn()
    try:
        session = db.get_auth_session(conn, auth_mod.token_hash(token))
        if not session or auth_mod.session_expired(session["expires_at"]):
            return None
        account = None
        row = conn.execute(
            "SELECT id, username, role, disabled FROM accounts WHERE id = ?",
            (session["account_id"],),
        ).fetchone()
        if row:
            account = {"id": row[0], "username": row[1], "role": row[2], "disabled": bool(row[3])}
        if account is None or account["disabled"]:
            return None
        return account
    finally:
        conn.close()


@app.middleware("http")
async def require_login(request: Request, call_next):
    """没登录：页面 → 302 到 /login；接口 → 401。"""
    if not app.state.auth_required:
        return await call_next(request)
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)
    account = _current_account(request)
    if account is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "未登录"}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    request.state.account = account
    return await call_next(request)

# 同一会话同一时间只允许一个回合在推进（同局串行保证状态一致；不同局可并行）
_advance_locks: dict[str, asyncio.Lock] = {}


def _lock_for(sid: str) -> asyncio.Lock:
    """会话级锁。原来是全局一把锁，两个账号同时玩会互相等。"""
    lock = _advance_locks.get(sid)
    if lock is None:
        lock = asyncio.Lock()
        _advance_locks[sid] = lock
    return lock


def _clock_label(session) -> str:
    """快照里的中文时间串（纯展示，不参与判定）。"""
    from ..clock import core as clock_mod

    try:
        phases = (session.loop.rules.clock or {}).get("phases") or {}
    except Exception:  # noqa: BLE001
        phases = {}
    return clock_mod.date_label(session.state.clock, phases)


def _weather_label(session) -> str:
    """天气中文名（真相在 `rules/clock.yaml: weather_effects`，前端不重复抄一份）。"""
    try:
        return session.loop.rules.weather_label(session.state.clock.weather)
    except Exception:  # noqa: BLE001
        return str(session.state.clock.weather or "")


@dataclass
class Session:
    sid: str
    pack: WorldPack
    cards: dict
    loop: GameLoop
    records: list[TurnRecord] = field(default_factory=list)
    events: Any = None
    conn: sqlite3.Connection | None = None
    status: str = "running"  # running | ended
    ending: dict | None = None
    # 二期：人类席位（None = 全 Agent 局）
    human: Any = None
    timeline: str = ""
    inherit_from: str = ""
    world_day: int = 1
    # P0：这一局属于谁（accounts.id；未认证为 'local'）
    owner: str = "local"
    # char_id → agent_id：经验挂在 agent 上而不是存档上，所以换局、换档都要沿用。
    # 接线前 `agent_ids_json` **只写不读**，等于这个映射从来不存在。
    char_to_agent: dict[str, str] = field(default_factory=dict)

    def experience_enabled(self) -> bool:
        """世界是否开了经验系统（`systems.experience`；缺省开启，关闭需显式声明）。"""
        try:
            from ..rulespack import assemble

            return assemble(self.pack).has("experience")
        except Exception:  # noqa: BLE001  装配失败不阻断对局
            return False

    def bind_agents(self, agent_ids: list[str] | None = None) -> dict[str, str]:
        """定下「哪个角色由哪个 agent 驱动」。

        优先用建局时显式绑定的 agent（`agent_ids_json`，**按席位顺序**的数组）；
        没绑的席位派生 `<world>:<char>`，保证同世界同席位跨局沿用经验。

        **诚实说明**：席位级 id 不是「persona 级身份」——真正的 persona 绑定要等 M20
        （联机与席位）。但在那之前，同世界同席位的经验已经能积累起来。
        """
        from ..engine.reflection import char_to_agent

        mapping = char_to_agent(self.cards, self.pack.world_id, agent_ids)
        self.char_to_agent = mapping

        # 回灌到 llm：局末 `reflect` 靠它决定经验写回哪个 agent
        bucket = getattr(self.loop.llm, "agent_ids", None)
        if isinstance(bucket, dict):
            bucket.update(mapping)
        return mapping

    def load_experience(self) -> int:
        """开局把每个角色的经验读进 prompt。返回读到的条数（0 = 没有或没开）。"""
        if self.conn is None or not self.experience_enabled():
            return 0

        from ..storage.experience import list_experience

        bucket = getattr(self.loop.llm, "experience", None)
        if not isinstance(bucket, dict):
            return 0

        total = 0
        for cid, agent_id in self.char_to_agent.items():
            items = list_experience(self.conn, agent_id, self.pack.world_id, limit=8)
            texts = [str(item.get("text") or "") for item in items if item.get("text")]
            if texts:
                bucket[cid] = texts
                total += len(texts)
        return total

    @property
    def state(self):
        return self.loop.state

    def _npc_label(self, npc_id: str) -> str:
        """NPC id → 中文名（查不到就原样返回，不编造）。"""
        for npc in getattr(self.pack, "npcs", None) or []:
            if str(npc.get("id")) == str(npc_id):
                return str(npc.get("label") or npc.get("name") or npc_id)
        return str(npc_id)

    def _secrets(self) -> dict:
        """世界包的隐藏信息可见性矩阵（缺省 = 不隐藏）。"""
        return dict(getattr(self.pack, "secrets", None) or {})

    def _visible_profession(self, cid: str, viewer: str | None) -> str:
        """职业名是否可见：`secrets.profession` 说了算（`self` = 只有本人看得见）。"""
        mode = str(self._secrets().get("profession") or "all")
        if mode == "self" and viewer is not None and viewer != cid:
            return ""
        return str(self.cards[cid].ext.profession or "")

    def _visible_path_label(self, cid: str, viewer: str | None, rt) -> str:
        """身份（path）与职业是一回事，同样受可见性约束。"""
        mode = str(self._secrets().get("profession") or "all")
        if mode == "self" and viewer is not None and viewer != cid:
            return ""
        return self.pack.path_label(rt.path or self.cards[cid].ext.path)

    def snapshot(self, viewer: str | None = None) -> dict[str, Any]:
        """对外快照。

        `viewer` 是**当前查看者**的角色 id；`None` 表示上帝/GM 视角（看全部）。
        隐藏身份按世界包的 `secrets` 矩阵裁剪：`secrets.profession: self` 意味着
        「只有本人看得见自己的职业」，别人的职业在快照里被抹掉——否则前端一打开
        就知道谁是预言家，信息不对称局当场作废。
        """
        st = self.state
        payload = {
            "session_id": self.sid,
            "world": {"id": st.world.world_id, "name": self.pack.name, "era": st.world.era},
            "region": st.scene.region,
            "location": st.scene.location_id,
            # 中文名牌：前端展示用；id 仍保留给 trace 与调试
            "region_label": self.pack.region_label(st.scene.region),
            "location_label": self.pack.location_label(st.scene.location_id, st.scene.region),
            "description": st.scene.description,
            "stage": st.stage,
            "tension": st.tension,
            "version": st.version,
            "clock": st.clock.model_dump(),
            # 中文时间串：前端不再自己拼，避免各页面显示不一致
            "clock_label": _clock_label(self),
            "weather_label": _weather_label(self),
            "party": st.party.model_dump(),
            "characters": [
                {
                    "id": cid,
                    "name": self.cards[cid].data.name,
                    "profession": self._visible_profession(cid, viewer),
                    "origin": self.cards[cid].ext.origin,
                    "origin_label": self.pack.origin_label(self.cards[cid].ext.origin),
                    "path": rt.path or self.cards[cid].ext.path,
                    "path_label": self._visible_path_label(cid, viewer, rt),
                    "talents": self.cards[cid].ext.talents,
                    # 左栏要看见自己带了什么：装备 tag 会改 DC，看不见等于规则不存在
                    "gear": [
                        str(getattr(e, "name_flavor", "") or getattr(e, "template_id", ""))
                        for e in (self.cards[cid].ext.equipment or [])
                    ],
                    "skills": dict(self.cards[cid].ext.skills or {}),
                    # 技能资源与私有笔记（A2）。笔记是「本人已确认的信息」，
                    # 只给本人与观察者（上帝 / 已出局复盘）——写进公共快照等于公开。
                    "skill_charges": dict(getattr(rt, "skill_charges", {}) or {}),
                    "notes": (
                        list(getattr(rt, "notes", []) or [])
                        if (_is_observer(self, viewer) or viewer == cid)
                        else []
                    ),
                    "flags": rt.flags,
                    "hp": rt.hp,
                    "hp_max": self.cards[cid].ext.hp_max,
                    "sanity": rt.sanity,
                    "position": rt.position.value,
                    "conditions": rt.conditions,
                    "gold": rt.gold,
                    "supplies": rt.supplies,
                    "controller": "human" if (self.human and self.human.is_human(cid)) else "llm",
                }
                for cid, rt in st.characters.items()
            ],
            # P4：右栏需要的东西（任务 / 在场的人 / 势力态度）
            "quests": [
                {
                    "id": q.id,
                    "title": q.title,
                    "status": getattr(q.status, "value", q.status),
                    "progress": q.progress,
                }
                for q in st.quests
            ],
            "present": [
                self._npc_label(nid) for nid in (st.scene.present_npcs or [])
            ],
            "reputation": dict(st.party.reputation or {}),
            "human_mode": getattr(self.human, "mode", "") if self.human else "",
            # 判定档位的中文 label（S1-3）：前端不该直接显示 `crit_success` 这种内部枚举
            "outcome_labels": self.loop.rules.outcome_labels(),
            "turns": len(self.records),
            "status": self.status,
            "timeline": self.timeline or st.world.timeline,
            "world_day": self.world_day,
            "human_ids": list(getattr(self.human, "human_ids", []) or []),
            "pending": self.human.pending() if self.human else None,
            "enemies": [e.model_dump(by_alias=True) for e in st.enemies.values()],
            "quests": [q.model_dump() for q in st.quests],
        }
        # **单一服务端出口**（S4-2 / E-2）：状态裁剪统一在这里做，
        # 不再散落在各处内联裁剪——一处裁剪，全端点一致。
        from .views import filter_for_viewer

        return filter_for_viewer(
            viewer,
            payload,
            secrets=self._secrets(),
            observer=_is_observer(self, viewer),
        )






def _card_from_form(pack, form: CharacterForm):
    """表单 → 角色卡。世界形态的差异由 `characters.card_from_form` 决定，接口层不做判断。"""
    data = form.model_dump()
    data.pop("world", None)
    return card_from_form(
        pack,
        origin_id=str(data.pop("origin", "") or ""),
        path_id=str(data.pop("path", "") or ""),
        profession=str(data.pop("profession", "") or ""),
        **data,
    )


def _default_world() -> str:
    """默认世界 = 第一个可用世界包（代码里不写死任何世界名）。"""
    worlds = discover_worlds(ROOT)
    return worlds[0] if worlds else ""


def _new_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout：多连接（测试 / 多会话）同时写库时等锁，而不是立刻 OperationalError
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
    db.init_db(conn)
    return conn


LOCAL_OWNER = "local"


def owner_of(request: Request | None = None) -> str:
    """归属键：已登录用 `accounts.id`；未认证（测试 / CLI / 关闭认证）回退 `'local'`。

    回退是为了让离线 CLI 与旧测试零改动；公网默认开认证，所以线上一定按账号隔离。
    """
    account = getattr(getattr(request, "state", None), "account", None)
    if isinstance(account, dict) and account.get("id"):
        return str(account["id"])
    return LOCAL_OWNER


def _get(sid: str, request: Request | None = None) -> Session:
    session = app.state.sessions.get(sid)
    if session is None:
        raise HTTPException(404, f"会话不存在：{sid}")
    if request is not None and session.owner != owner_of(request):
        # 不暴露“存在但不属于你”
        raise HTTPException(404, f"会话不存在：{sid}")
    return session


# ── 元信息 ──────────────────────────────────────────────────

def _is_observer(session: Session, viewer: str | None) -> bool:
    """查看者是不是「观察者」（上帝视角）：能看全部，不受分组私密限制。

    三条都属「不参与本局博弈」的情形：

    - 没传 `viewer`：页面没以任何角色身份在看（主持人 / 复盘视角）；
    - `viewer` 不在本局角色表里：陈旧链接或纯旁观；
    - 该角色已出局（含完成遗言）：不再参与博弈，理应能看全局——这是「死后复盘」
      的正当需求，不是泄密。濒死仍算参与方，不算观察者。

    **为什么抽成一个函数**：思维过程（`_thoughts_visible`）与出站事件裁剪
    （`routes/play.py` 里带 `audience` 的事件）必须是同一份判定，否则会出现
    「盘算看不到、私下商议却漏给他」这种一半遮一半漏的组合。
    """
    if viewer is None:
        return True

    runtime = session.state.characters.get(viewer)
    if runtime is None:
        return True
    return bool(is_eliminated(runtime))


def _thoughts_visible(session: Session, viewer: str | None, cid: str) -> bool:
    """这个查看者能不能看到某个角色的思维过程（thought）。

    三档视角：
    - **上帝视角**（观察者，见 `_is_observer`）：全部可见。
    - **参与者视角**（`viewer` 是某个在场角色）：按世界包 `visibility.thoughts` 决定
      - `god`：看不到别人的（只看到自己的）——信息对抗类世界用这个
      - `participant`：互相可见——推理类世界用这个
      - `all`：全部可见（**缺省**，存量世界因此完全不变）
    - **出局者回到上帝视角**：角色死亡（完成遗言）后不再参与博弈，
      理应能看到全局——这是「死后复盘」的正当需求，不是泄密。
    """
    if _is_observer(session, viewer):
        return True

    config = getattr(session.pack, "visibility", None) or {}
    mode = str(config.get("thoughts") or "all")
    if mode == "god":
        return cid == viewer
    return True


def _turn_payload(session: Session, rec: TurnRecord, viewer: str | None = None) -> dict:
    """一回合的对外结果（同步接口与 SSE 共用同一份结构）。"""
    actors = []
    for i, cid in enumerate(rec.actors):
        card = session.cards[cid]
        thought = ""
        if i < len(rec.thoughts) and _thoughts_visible(session, viewer, cid):
            thought = rec.thoughts[i]
        actors.append(
            {
                "id": cid,
                "name": card.data.name,
                "profession": card.ext.profession,
                "thought": thought,
                "action": rec.actions[i] if i < len(rec.actions) else "",
                "outcome": rec.outcomes[i] if i < len(rec.outcomes) else "",
                "narration": rec.narration[i] if i < len(rec.narration) else "",
            }
        )
    payload = {
        "turn": rec.turn,
        "scene": rec.scene,
        # 结构化可选行动：前端渲染成按钮，人类点一下就能提交
        "options": list(getattr(rec, "options", []) or []),
        "actors": actors,
        "rejected": rec.rejected,
        "elapsed_ms": rec.elapsed_ms,
        "declare_ms": rec.declare_ms,
        "loot": rec.loot,
        "combat": rec.combat,
        "deaths": rec.deaths,
        "path_changes": rec.path_changes,
        # 集体决策的票型与计票结果（C57）：前端决策面板与复盘页都要用
        "votes": rec.votes,
        "vote_result": rec.vote_result,
        # 判定档位中文 label（S1-3）：前端按 id 查表，不必自己翻译内部枚举
        "outcome_labels": session.loop.rules.outcome_labels(),
        "state": session.snapshot(viewer),
    }
    # **单一服务端出口**（S4-2）：回合结果里的角色清单也要裁身份
    # （`secrets.profession: self` 时别人的职业不能出现在这一份里）。
    from .views import filter_for_viewer

    return filter_for_viewer(
        viewer,
        payload,
        secrets=session._secrets(),
        observer=_is_observer(session, viewer),
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

def _legacy_block(conn: sqlite3.Connection, pack, inherit_from: str = "") -> str:
    """本局 GM 要知道的「之前发生过什么」。

    两段来源：
    1. 继承的上一局（`inherit_from`）→ 结局传闻
    2. 本世界账本里已发生的事（跨局）

    抽成独立函数是为了能单测（纯函数化：conn + pack → 文本）。
    """
    parts: list[str] = []
    if inherit_from:
        text = _legacy_text(conn, inherit_from)
        if text:
            parts.append(text)
    try:
        ledger_text = world_ledger.summary(conn, pack.world_id)
    except Exception:  # noqa: BLE001
        ledger_text = ""
    if ledger_text:
        parts.append("这个世界已经发生过的事：\n" + ledger_text)
    return "\n\n".join(parts)


def _legacy_text(conn: sqlite3.Connection, from_sid: str) -> str:
    """把上一局的结局转成一段「传闻」，注入本局 GM 的 system。

    只传故事，不传数值与人物——数值与人物由本局重新生成（方案 §四）。
    """
    saved = db.load_ending(conn, from_sid)
    if not saved:
        return ""
    payload = saved.get("payload") or {}
    kind = "全灭" if payload.get("kind") == "wipe" else "收场"
    people = payload.get("characters") or []
    survivors = [str(p.get("name") or p.get("id") or "") for p in people if p.get("name")]
    tail = str(payload.get("epilogue") or "").strip().replace("\n", " ")
    head = f"上一局（{from_sid}）以{kind}告终。"
    if survivors:
        head += f"当事人：{'、'.join(survivors[:5])}。"
    if tail:
        head += f"事后流传的说法：{tail[:240]}"
    return head


def _reflect_experience(session: Session, payload: dict) -> int:
    """局末自反思：调引擎层的 `reflect_all`（API 与 CLI 共用同一份实现）。

    以前这段逻辑只写在 API 层，结果 CLI 跑出来的局**永远不产生经验**——
    实测真实模型 8 人 6 回合后 `agent_experience` 是 0 条。
    """
    from ..engine.reflection import reflect_all

    return reflect_all(
        session.loop,
        session.records,
        str(payload.get("epilogue") or payload.get("summary") or ""),
        session.char_to_agent,
        session.conn,
        session.pack.world_id,
        sid=session.sid,
        pack=session.pack,
    )


def _finish(session: Session, kind: str = "ended", turn: int = 0, rec=None) -> dict:
    """结算：结构化快照 + 结局文本，落库并标记会话结束。"""
    state = session.loop.state
    payload = ending_mod.summary(state, session.cards, kind, turn)

    text = ""
    hook = getattr(session.loop.llm, "epilogue", None)
    if callable(hook):
        try:
            text = str(hook(state, session.cards, kind, turn) or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
    if not text:
        text = ending_mod.epilogue_text(state, session.cards, kind)
    payload["epilogue"] = text
    # 局末自反思（Reflexion）：每个 agent 写下这一局学到的东西，写回它自己的档案。
    # 放在 epilogue 之后：经验要引用结局，不能反过来。
    payload["experience_learned"] = _reflect_experience(session, payload)

    session.status = "ended"
    session.ending = payload
    if session.conn is not None:
        # 三期：世界账本——玩家死亡 / 身份授予 / 结局都记进这个世界
        try:
            wid = session.pack.world_id
            for cid in [d.get("char_id") for d in (rec.deaths if rec else [])]:
                if cid:
                    world_ledger.write(
                        session.conn, wid, f"player_death:{cid}", "player_death",
                        {"char_id": cid, "summary": f"角色 {cid} 死在了这一局"},
                        session_id=session.sid, turn=turn,
                    )
            for change in (rec.path_changes if rec else []):
                world_ledger.write(
                    session.conn, wid, f"identity:{change.get('char_id')}", "identity",
                    {"char_id": change.get("char_id"), "path": change.get("path"),
                     "summary": f"{change.get('char_id')} 拿到了身份「{change.get('label')}」"},
                    session_id=session.sid, turn=turn,
                )
            world_ledger.write(
                session.conn, wid, f"ending:{session.sid}", "ending",
                {"kind": kind, "turn": turn, "summary": str(payload.get("epilogue") or "")[:160]},
                session_id=session.sid, turn=turn,
            )
        except Exception:  # noqa: BLE001
            pass
        # 三期：活到收场才发局外点（全灭不发）
        if kind == "ended":
            try:
                hooks = load_hooks(session.pack.root, session.pack.world_id)
                meta_earn.apply_events(
                    hooks,
                    Wallet(session.conn),
                    ["ending"],
                    session_id=session.sid,
                    world_id=hooks.world_id,
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            db.save_ending(
                session.conn,
                session.sid,
                kind,
                turn,
                payload,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            db.set_session_status(session.conn, session.sid, "ended")
        except Exception:  # noqa: BLE001
            pass
    return payload


def _require_unlocked(conn: sqlite3.Connection, sid: str) -> None:
    """改配置前必须过这道门：已锁定的局一律 409（前端藏控件不算数）。"""
    meta = db.session_meta(conn, sid)
    if meta.get("config_locked"):
        raise HTTPException(409, "本局配置已锁定（开局时定型），不能再改")

def _serve_page(name: str) -> str:
    """静态页：优先用 Vue 构建产物，没有就回退旧的单文件页面。

    为什么要两条路：构建产物（`static/dist/`）与 `node_modules/` 都不进仓库，
    所以任何环境——包括没装 Node 的 CI——都能正常开页面；装了依赖的开发者跑一次
    `npm run build` 就自动切到新的 Vue 版本。两条路的内容都是同一套契约。
    """
    dist = ROOT / "static" / "dist" / name
    if dist.is_file():
        return dist.read_text(encoding="utf-8")
    page = ROOT / "static" / name
    if not page.exists():
        return f"<h1>ai-trpg</h1><p>static/{name} 不存在</p>"
    return page.read_text(encoding="utf-8")

__all__ = sorted(name for name in globals() if not name.startswith("__"))
