"""PC Agent 必须命中 `tiers.pc` 分档（B23）。

以前 PC 走独立 `resolve()`，返回的 spec 恒不等于 `default_spec`，
`_chat_json` 的分档判断永远不命中，3 个 PC 成了最贵且最不受控的一段。
"""

import json
from pathlib import Path

from trpg.agents import AgentLLM, LLMConfig
from trpg.agents.providers.base import ChatResult
from trpg.models import ModelConfig
from trpg.world import load_world
from support.builders import make_card

ROOT = Path(__file__).resolve().parents[1]
PACK = load_world("ironvale", ROOT)
PLAYER_JSON = {
    "type": "final",
    "action": "观察四周的动静",
    "approach": "贴着墙，压低呼吸",
    "skill": "观察",
    "intent": "careful",
}


class RecAdapter:
    kind = "rec"

    def __init__(self) -> None:
        self.seen: list[dict] = []

    def chat(self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None):
        self.seen.append(
            {"model": model, "max_tokens": max_tokens, "override": extra_override}
        )
        return ChatResult(
            content=json.dumps(PLAYER_JSON, ensure_ascii=False),
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            model=model,
        )


def _agent(config: LLMConfig | None = None) -> tuple[AgentLLM, RecAdapter]:
    cfg = config or LLMConfig(ROOT)
    adapter = RecAdapter()
    cfg._adapters = {"minimax": adapter}
    return AgentLLM(cfg, {"chr_a": make_card()}, seeds=PACK.seeds, seed="42"), adapter


def test_pc_spec_uses_pc_tier():
    agent, _ = _agent()
    tier = agent.config.tier("pc")
    assert tier, "config/providers.yaml 必须有 tiers.pc，否则 PC 仍不受控"

    spec = agent._pc_spec(make_card())
    assert spec.reasoning_effort == tier["reasoning_effort"]
    assert spec.max_tokens == tier["max_tokens"]


def test_pc_spec_keeps_explicit_card_model():
    """角色卡显式选了模型，分档不得把它改掉（只调思考深度与预算）。"""
    agent, _ = _agent()
    card = make_card()
    card.ext.model = ModelConfig(provider="deepseek", model="deepseek-chat")

    spec = agent._pc_spec(card)
    assert (spec.provider, spec.model) == ("deepseek", "deepseek-chat")
    assert spec.reasoning_effort == agent.config.tier("pc")["reasoning_effort"]


def test_pc_tier_falls_back_to_npc(tmp_path):
    """没配 tiers.pc 的旧配置必须回落 npc 档，而不是回到「不受控」。"""
    path = tmp_path / "providers.yaml"
    path.write_text(
        "defaults:\n  provider: minimax\n  model: m\n"
        "tiers:\n  npc:\n    reasoning_effort: low\n    max_tokens: 600\n",
        encoding="utf-8",
    )
    agent, _ = _agent(LLMConfig(ROOT, config_path=path))

    spec = agent._pc_spec(make_card())
    assert spec.reasoning_effort == "low"
    assert spec.max_tokens == 600


def test_player_turn_uses_tier_budget():
    """真正发出去的请求必须带分档预算与思考深度，而不只是对象内部对。"""
    agent, adapter = _agent()
    tier = agent.config.tier("pc")
    card = make_card()

    agent.run_agent_turn("chr_a", card, make_state(), 1)

    assert adapter.seen, "玩家回合至少要发一次请求"
    assert adapter.seen[0]["max_tokens"] == tier["max_tokens"]
    assert adapter.seen[0]["override"] == {"reasoning_effort": tier["reasoning_effort"]}


def make_state():
    from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef

    card = make_card()
    return GameState(
        session_id="ses_tier",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=1, supplies=1),
        characters={"chr_a": CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)},
    )
