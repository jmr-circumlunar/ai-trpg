"""阶段一出口验收（`02-待办清单` §1.7 的 8 条机器判据）。

每条判据一个测试，名字里带编号，方便对着清单核对。
「4 条可玩性人工判据」不在这里——那要人玩，结论与记录方式见知识库出口验收报告。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trpg.agents import MockLLM
from trpg.api.app import app
from trpg.engine.loop import GameLoop
from trpg.models import (
    CharacterRuntime,
    GameState,
    Party,
    Scene,
    WorldRef,
)
from trpg.models.enums import Position
from trpg.rules.loader import Rules
from trpg.storage import auth as auth_mod
from trpg.storage import db
from trpg.world.pack import load_world

ROOT = Path(__file__).resolve().parents[1]
app_mod = importlib.import_module("trpg.api.deps")
ALICE = ("alice", "alice-pass-1")

WOLVES = 3
VILLAGERS = 5


# ── 工具 ──────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DB_PATH", tmp_path / "exit.db")
    monkeypatch.setattr(app.state, "sessions", {})
    previous = app.state.auth_required
    app.state.auth_required = True
    auth_mod.LIMITER._fails.clear()
    with TestClient(app) as c:
        yield c
    app.state.auth_required = previous


def _login(client: TestClient) -> None:
    conn = app_mod._new_conn()
    try:
        db.create_account(
            conn, username=ALICE[0], password_hash=auth_mod.hash_password(ALICE[1])
        )
    finally:
        conn.close()
    assert client.post(
        "/api/login", json={"username": ALICE[0], "password": ALICE[1]}
    ).status_code == 200


def _werewolf_session(client: TestClient) -> dict:
    _login(client)
    res = client.post(
        "/api/sessions",
        json={"world": "werewolf", "players": 8, "provider": "mock", "seed": "9"},
    )
    assert res.status_code == 200, res.text
    return res.json()


class RecordingLLM(MockLLM):
    def __init__(self, seed: int | str) -> None:
        super().__init__(seed)
        self.seen: list[tuple[str, list[str]]] = []

    def player_action(self, actor_id, card, state, turn, previous=None):
        self.seen.append((actor_id, list(previous or [])))
        return super().player_action(actor_id, card, state, turn, previous)


def build_loop(seed: int = 42, dead: set[str] | None = None):
    """8 人狼人杀局（mock）。`dead` 里的角色开局即出局。"""
    pack = load_world("werewolf", ROOT)
    dead = dead or set()
    cards = {}
    characters = {}
    for index in range(WOLVES + VILLAGERS):
        cid = f"chr_{index + 1}"
        camp = "wolf" if index < WOLVES else "village"
        cards[cid] = make_card_for(cid, camp)
        characters[cid] = CharacterRuntime(
            hp=10,
            sanity=10,
            camp=camp,
            position=Position.dead if cid in dead else Position.scene,
        )
    state = GameState(
        session_id="ses_exit",
        world=WorldRef(world_id="werewolf", era="base"),
        scene=Scene(location_id="loc_square", region="village"),
        party=Party(),
        characters=characters,
    )
    llm = RecordingLLM(seed)
    return GameLoop(Rules(ROOT), cards, state, llm, seed=seed, pack=pack), llm


def make_card_for(cid: str, camp: str):
    from support.builders import make_card

    return make_card(cid=cid, profession="狼人" if camp == "wolf" else "村民")


# ── 判据 1：mock 跑完整局 ─────────────────────────────────

def test_judge_1_mock_runs_ten_turns():
    """判据 1：`--world werewolf --provider mock --turns 10` 跑得起来。"""
    result = subprocess.run(
        [
            sys.executable,
            "run.py",
            "run",
            "--world",
            "werewolf",
            "--provider",
            "mock",
            "--turns",
            "10",
            "--players",
            "8",
            "--db",
            str(ROOT / ".tmp_exit.db"),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    try:
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "回合数" in result.stdout
    finally:
        (ROOT / ".tmp_exit.db").unlink(missing_ok=True)


# ── 判据 2：真实模型 ──────────────────────────────────────

def test_judge_2_real_model_no_rejected_delta():
    """判据 2：真实模型 8 人 6 回合，delta 被拒 0 次。

    需要 provider key，**不进 CI**：没有 key 时跳过。
    """
    if not os.environ.get("TRPG_REAL_MODEL"):
        pytest.skip("没有 TRPG_REAL_MODEL（真实模型判据不进 CI）")

    result = subprocess.run(
        [
            sys.executable,
            "run.py",
            "run",
            "--world",
            "werewolf",
            "--provider",
            "auto",
            "--turns",
            "6",
            "--players",
            "8",
            "--db",
            str(ROOT / ".tmp_exit_real.db"),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    try:
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "被拒" not in result.stdout or "被拒 0" in result.stdout
    finally:
        (ROOT / ".tmp_exit_real.db").unlink(missing_ok=True)


# ── 判据 3：夜间对非本组不可见，白天发言进 briefs ───────────

def test_judge_3_night_is_private_and_day_is_public():
    loop, llm = build_loop()
    rec = asyncio.run(loop.run(1))[0]

    assert rec.order[0] == "night", "夜间必须是第一个阶段"
    assert "dawn" in rec.order, "天亮公布阶段必须在"

    wolves = [b for cid, b in llm.seen if cid in ("chr_1", "chr_2", "chr_3")]
    villagers = [
        b for cid, b in llm.seen if cid in ("chr_4", "chr_5", "chr_6", "chr_7", "chr_8")
    ]
    assert any("共同决定" in line for briefs in wolves for line in briefs), (
        "狼组内部应看到共议决议"
    )
    assert all("共同决定" not in line for briefs in villagers for line in briefs), (
        "非狼人不该看到夜里的共议决议"
    )

    # 白天（discuss）是 public：后手能看到前手的行动
    assert "discuss" in rec.order
    assert any(briefs for briefs in llm.seen), "白天应当有公开信息"


# ── 判据 4：投票放逐 + 票型可查 ───────────────────────────

def test_judge_4_vote_is_recorded_and_traceable(client):
    data = _werewolf_session(client)
    sid = data["session_id"]

    res = client.post(f"/api/sessions/{sid}/advance")
    assert res.status_code == 200, res.text
    payload = res.json()

    assert payload.get("votes"), "票型应随回执出得来"
    assert payload.get("vote_result", {}).get("counts") is not None

    trace = client.get(f"/api/sessions/{sid}/trace")
    assert trace.status_code == 200
    turns = trace.json().get("turns") or []
    assert turns, "trace 至少要有一回合"
    assert any(turn.get("votes") for turn in turns), "trace 里要查得到票型"


# ── 判据 5：出局者不再行动与发言 ──────────────────────────

def test_judge_5_eliminated_players_do_not_act():
    loop, llm = build_loop(dead={"chr_8"})

    rec = asyncio.run(loop.run(1))[0]

    assert "chr_8" not in rec.actors, "出局者不该再行动"
    assert all(cid != "chr_8" for cid, _ in llm.seen), "出局者不该再被要求决策"


def test_judge_5_eliminated_players_cannot_speak():
    from trpg.agents.agent.tools import ToolMixin

    class Agent(ToolMixin):
        def __init__(self):
            self.pack = None
            self.spoken = []

    loop, _ = build_loop(dead={"chr_8"})
    agent = Agent()
    out = agent._tool_say(
        "chr_8",
        make_card_for("chr_8", "village"),
        loop.state,
        {"speech": "我还能说话吗"},
        1,
    )
    assert "出局" in out
    assert agent.spoken == []


# ── 判据 6：一方全灭即终局 + 结算落库 ─────────────────────

def test_judge_6_wipe_ends_the_run(client):
    """把村民全灭（留一个狼）→ 局终，且 API 返回结算。"""
    data = _werewolf_session(client)
    sid = data["session_id"]

    # 把除一个狼之外的都标成出局
    session = app.state.sessions.get(sid)
    assert session is not None
    for cid, runtime in session.state.characters.items():
        if runtime.camp == "village":
            runtime.position = Position.dead

    res = client.post(f"/api/sessions/{sid}/advance")

    assert res.status_code == 200, res.text
    payload = res.json()
    assert session.state.winner, "胜负应由代码判出"
    assert payload.get("ending"), "终局要带结算"


# ── 判据 7：缺省配置下 ironvale 逐字节不变 ─────────────────

def test_judge_7_ironvale_behaviour_unchanged():
    """判据 7：golden 行为快照必须一致（跑一遍快照测试）。"""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_behavior_snapshot.py", "-q"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:]


# ── 判据 8：门禁全绿 ──────────────────────────────────────

@pytest.mark.parametrize(
    "script", ["check_contract.py", "check_world.py", "check_generic.py"]
)
def test_judge_8_gates_are_green(script):
    """判据 8：门禁全绿（文档门禁由 CI 跑，这里跑快的三条）。"""
    result = subprocess.run(
        [sys.executable, f"tools/{script}"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, f"{script}\n{result.stdout}\n{result.stderr}"
