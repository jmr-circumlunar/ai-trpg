"""事件种子：按 era/幕次筛选、按权重抽样、不重复、注入 prompt。"""

import json
from pathlib import Path

from trpg.llm import AgentLLM, LLMConfig
from trpg.llm.providers.base import ChatResult
from trpg.models import GameState, Scene, WorldRef
from trpg.testing import make_card
from trpg.world import load_world

ROOT = Path(__file__).resolve().parents[1]
PACK = load_world("ironvale", ROOT)


class Rec:
    kind = "rec"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def chat(self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None):
        self.calls.append(messages)
        return ChatResult(
            content=json.dumps(
                {"narration": "场景。", "hooks": [], "present_npcs": []},
                ensure_ascii=False,
            ),
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def _agent(seed="42") -> tuple[AgentLLM, Rec]:
    cfg = LLMConfig(ROOT)
    rec = Rec()
    cfg._adapters = {"minimax": rec}
    agent = AgentLLM(
        cfg, {"chr_a": make_card()}, seeds=PACK.seeds, seed=seed
    )
    return agent, rec


def _state(stage: int = 1) -> GameState:
    st = GameState(
        session_id="s",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
    )
    st.stage = stage
    return st


def test_pick_seed_respects_stage():
    agent, _ = _agent()
    picked = agent._pick_seed(_state(stage=1))
    assert picked is not None
    lo, hi = picked["stage_range"][:2]
    assert lo <= 1 <= hi


def test_pick_seed_no_repeat():
    agent, _ = _agent()
    ids = set()
    for _ in range(len(PACK.seeds)):
        s = agent._pick_seed(_state(stage=2))
        if s is None:
            break
        ids.add(s["id"])
    # 抽完不重复
    assert len(ids) == len(agent.seed_log)
    assert len({x["id"] for x in agent.seed_log}) == len(agent.seed_log)


def test_pick_seed_exhausted_returns_none():
    agent, _ = _agent()
    for _ in range(len(PACK.seeds) + 3):
        agent._pick_seed(_state(stage=2))
    assert agent._pick_seed(_state(stage=2)) is None


def test_seed_injected_into_prompt():
    agent, rec = _agent()
    agent.describe_scene(_state(), 1)
    prompt = json.dumps(rec.calls[0], ensure_ascii=False)
    assert "本回合可参考的剧情骨架" in prompt
    assert agent.seed_log, "应记录抽到的种子"


def test_same_seed_same_pick():
    a, _ = _agent("7")
    b, _ = _agent("7")
    assert [a._pick_seed(_state()) for _ in range(3)] == [
        b._pick_seed(_state()) for _ in range(3)
    ]
