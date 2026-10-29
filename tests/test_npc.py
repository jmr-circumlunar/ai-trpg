"""NPC 作为独立 Agent：GM 只能申报合法 NPC，NPC 自己按目标行动。"""

import asyncio
import json
from pathlib import Path

from trpg.engine.loop import GameLoop
from trpg.agents import AgentLLM, LLMConfig, MockLLM
from trpg.agents.providers.base import ChatResult
from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef
from trpg.rules.loader import Rules
from support.builders import make_card
from trpg.world import load_world

ROOT = Path(__file__).resolve().parents[1]
PACK = load_world("ironvale", ROOT)
NPCS = PACK.npcs


class Scripted:
    """按 system prompt 里的关键词返回不同 payload。"""

    kind = "script"

    def __init__(self, present=None) -> None:
        self.present = present or []
        self.systems: list[str] = []

    def chat(self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None):
        system = messages[0]["content"]
        self.systems.append(system)
        if "describe_scene" in messages[-1]["content"]:
            payload = {"narration": "货栈里有人。", "present_npcs": self.present}
        elif "narrate_result" in messages[-1]["content"]:
            payload = {"narrations": [{"actor_id": "chr_p1", "text": "盗贼退开。"}]}
        elif "你在扮演" in system:
            payload = {"action": "把账本塞进袖口", "speech": "这儿没你要的东西。"}
        else:
            payload = {"thought": "想", "action": "做", "skill": "观察", "intent": "normal"}
        return ChatResult(
            content=json.dumps(payload, ensure_ascii=False),
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def _agent(adapter) -> AgentLLM:
    cfg = LLMConfig(ROOT)
    cfg._adapters = {"minimax": adapter}
    cards = {"chr_p1": make_card()}
    return AgentLLM(cfg, cards, npcs=NPCS)


def test_validate_drops_unknown_ids():
    agent = _agent(Scripted())
    assert agent._validate_npcs(["npc_not_exist"]) == []


def test_validate_caps_at_two():
    agent = _agent(Scripted())
    ids = [n["id"] for n in NPCS]
    assert len(agent._validate_npcs(ids)) == 2


def test_scene_reports_present_npcs():
    agent = _agent(Scripted(present=["npc_fishwife"]))
    scene = agent.describe_scene(
        GameState(
            session_id="s",
            world=WorldRef(world_id="ironvale", era="medieval"),
            scene=Scene(location_id="loc_a", region="north_port"),
        ),
        1,
    )
    assert [n["id"] for n in scene["present_npcs"]] == ["npc_fishwife"]


def test_npc_action_returns_speech():
    agent = _agent(Scripted())
    move = agent.npc_action(NPCS[0], GameState(
        session_id="s",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
    ), 1)
    assert move["action"]
    assert move["speech"]


def test_npc_prompt_states_own_goal():
    adapter = Scripted()
    agent = _agent(adapter)
    state = GameState(
        session_id="s",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
    )
    agent.npc_action(NPCS[0], state, 1)
    system = adapter.systems[-1]
    assert NPCS[0]["goal"] in system
    assert "只为自己的目标行动" in system


def test_loop_runs_present_npcs():
    """loop 应把在场 NPC 各自跑一遍，并把动向传给 GM 叙事。"""

    class LoopLLM(MockLLM):
        def describe_scene(self, state, turn):
            return {"narration": "货栈。", "present_npcs": NPCS[:1]}

        def npc_action(self, npc, state, turn):
            self.npc_called = getattr(self, "npc_called", 0) + 1
            return {"id": npc["id"], "name": npc["name"], "action": "看过来", "speech": "谁"}

    rules = Rules(ROOT)
    cards = {"chr_p1": make_card()}
    state = GameState(
        session_id="ses_npc",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=1, supplies=1),
        characters={"chr_p1": CharacterRuntime(hp=11, sanity=8)},
    )
    llm = LoopLLM(42)
    rec = asyncio.run(GameLoop(rules, cards, state, llm, seed=42).run(1))[0]

    assert getattr(llm, "npc_called", 0) == 1
    assert rec.npc_moves and rec.npc_moves[0]["name"]
