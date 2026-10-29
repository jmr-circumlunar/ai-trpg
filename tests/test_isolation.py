"""信息隔离：一个角色的「思考」与「私有目标」绝不能进别人的 prompt。

这是多 Agent 跑团唯一的底线——一旦串台，角色就变成同一个人的分身。
"""

import json
from pathlib import Path

from trpg.agents import AgentLLM, LLMConfig
from trpg.agents.providers.base import ChatResult
from support.builders import make_card, make_state

ROOT = Path(__file__).resolve().parents[1]


class QueueAdapter:
    """按顺序返回预设 payload，并记录每次收到的 messages。"""

    kind = "queue"

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls: list[list[dict]] = []

    def chat(
        self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None
    ) -> ChatResult:
        self.calls.append(messages)
        payload = self.payloads.pop(0) if self.payloads else {}
        return ChatResult(
            content=json.dumps(payload, ensure_ascii=False),
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def build_agent(payloads: list[dict]):
    cfg = LLMConfig(ROOT)
    cards = {
        "chr_a": make_card(),
        "chr_b": make_card(profession="战士"),
    }
    adapter = QueueAdapter(payloads)
    cfg._adapters = {"minimax": adapter}
    return AgentLLM(cfg, cards), cards, adapter


def test_thought_not_leaked_to_other_agent():
    agent, cards, adapter = build_agent(
        [
            {"thought": "SECRET_A_ONLY", "action": "a", "skill": "观察", "intent": "normal"},
            {"thought": "b", "action": "b", "skill": "潜行", "intent": "careful"},
        ]
    )
    state = make_state()

    first = agent.player_action("chr_a", cards["chr_a"], state, 1)
    assert first.thought == "SECRET_A_ONLY"

    agent.player_action("chr_b", cards["chr_b"], state, 1)

    second_prompt = json.dumps(adapter.calls[1], ensure_ascii=False)
    assert "SECRET_A_ONLY" not in second_prompt, "A 的思考泄露进了 B 的 prompt"


def test_private_goal_not_leaked():
    agent, cards, adapter = build_agent(
        [
            {"thought": "t", "action": "a", "skill": "观察"},
            {"thought": "t", "action": "b", "skill": "潜行"},
        ]
    )
    cards["chr_a"].ext.private_goal = "GOAL_A_SECRET"
    cards["chr_b"].ext.private_goal = "GOAL_B_SECRET"

    state = make_state()
    agent.player_action("chr_a", cards["chr_a"], state, 1)
    agent.player_action("chr_b", cards["chr_b"], state, 1)

    second_prompt = json.dumps(adapter.calls[1], ensure_ascii=False)
    assert "GOAL_A_SECRET" not in second_prompt
    assert "GOAL_B_SECRET" in second_prompt  # 自己的目标必须能看到


def test_own_goal_visible():
    agent, cards, adapter = build_agent([{"thought": "t", "action": "a", "skill": "观察"}])
    cards["chr_a"].ext.private_goal = "MY_OWN_GOAL"
    agent.player_action("chr_a", cards["chr_a"], make_state(), 1)

    first_prompt = json.dumps(adapter.calls[0], ensure_ascii=False)
    assert "MY_OWN_GOAL" in first_prompt
