"""上下文三层：近期原文（2 回合）+ 分章回顾（每 5 回合一条）+ 硬事实。"""

import asyncio
import json
from pathlib import Path

from trpg.engine.loop import GameLoop
from trpg.agents import AgentLLM, LLMConfig, MockLLM
from trpg.memory.settings import MemorySettings
from trpg.agents.providers.base import ChatResult
from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef
from trpg.rules.loader import Rules
from support.builders import make_card, make_state

ROOT = Path(__file__).resolve().parents[1]
# 摘要周期与保留条数的唯一真相在 config/memory.yaml，代码里不再有第二份常量
_SETTINGS = MemorySettings.load(ROOT)
SUMMARY_EVERY = _SETTINGS.summary_every
SUMMARY_KEEP = _SETTINGS.chapter_keep


def _loop(llm) -> GameLoop:
    rules = Rules(ROOT)
    cards = {"chr_p1": make_card(), "chr_p2": make_card(profession="战士")}
    state = GameState(
        session_id="ses_ctx",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=10, supplies=2),
        characters={
            cid: CharacterRuntime(hp=c.ext.hp, sanity=c.ext.sanity)
            for cid, c in cards.items()
        },
    )
    return GameLoop(rules, cards, state, llm, seed=42)


def test_mock_summarize_every_window():
    llm = MockLLM(42)
    for t in range(1, 6):
        llm.maybe_summarize(t)
    assert len(llm._summaries) == 1  # 只在第 5 回合生成
    for t in range(6, 11):
        llm.maybe_summarize(t)
    assert len(llm._summaries) == 2


def test_summaries_capped():
    llm = MockLLM(42)
    for t in range(1, 60):
        llm.maybe_summarize(t)
    assert len(llm._summaries) <= SUMMARY_KEEP


def test_loop_triggers_summarize():
    llm = MockLLM(42)
    asyncio.run(_loop(llm).run(SUMMARY_EVERY))
    assert len(llm._summaries) == 1


class RecordingAdapter:
    kind = "rec"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def chat(self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None):
        self.calls.append(messages)
        payload = {
            "thought": "想",
            "action": "做",
            "skill": "观察",
            "intent": "normal",
        }
        # 摘要请求返回 summaries 结构
        if "压缩" in json.dumps(messages, ensure_ascii=False):
            payload = {"summaries": [{"text": "队伍进了钟楼", "importance": 6}]}
        return ChatResult(
            content=json.dumps(payload, ensure_ascii=False),
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def test_real_agent_summarize_and_inject():
    cfg = LLMConfig(ROOT)
    cards = {"chr_a": make_card()}
    rec = RecordingAdapter()
    cfg._adapters = {"minimax": rec}

    agent = AgentLLM(cfg, cards)
    agent._recent_public = ["回合1的场景", "回合2的场景"]

    agent.maybe_summarize(5)
    assert agent._summaries, "第 5 回合应生成一条回顾"
    assert "第1~5回合" in agent._summaries[0]

    rec.calls.clear()
    agent.player_action("chr_a", cards["chr_a"], make_state(), 6)
    prompt = json.dumps(rec.calls[0], ensure_ascii=False)
    assert "更早发生了什么" in prompt
    assert "队伍进了钟楼" in prompt


def test_recent_public_only_two_turns_in_prompt():
    cfg = LLMConfig(ROOT)
    cards = {"chr_a": make_card()}
    rec = RecordingAdapter()
    cfg._adapters = {"minimax": rec}
    agent = AgentLLM(cfg, cards)
    agent._recent_public = [f"第{i}回合的叙述" for i in range(1, 8)]

    agent.player_action("chr_a", cards["chr_a"], make_state(), 8)
    prompt = json.dumps(rec.calls[0], ensure_ascii=False)
    assert "第7回合的叙述" in prompt
    assert "第1回合的叙述" not in prompt  # 早期原文已被丢弃，靠摘要
