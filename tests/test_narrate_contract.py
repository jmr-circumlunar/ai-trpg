"""叙事返回字段与消费点一一对应（B22 / C25）。

约束：prompt 要求模型申报的每个字段，必须在返回结构里透传，且只在一处被代码消费。
敌人申报唯一入口 = SCENE（C25），NARRATE 阶段不再接受敌人。
"""

import asyncio
import json
from pathlib import Path

from trpg.agents import AgentLLM, LLMConfig, MockLLM
from trpg.agents.providers.base import ChatResult
from trpg.engine.loop import GameLoop
from trpg.models import CharacterRuntime, GameState, Party, PlayerAction, Scene, WorldRef
from trpg.models.enums import Intent
from trpg.rules import DiceEngine, Rules
from trpg.rules.judge import judge
from trpg.world import load_world
from support.builders import make_card

ROOT = Path(__file__).resolve().parents[1]
PACK = load_world("ironvale", ROOT)


class Canned:
    """按顺序吐固定 JSON 的假 provider。"""

    kind = "canned"

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls: list[list[dict]] = []

    def chat(self, messages, model, temperature=0.9, max_tokens=1200, extra_override=None):
        self.calls.append(messages)
        payload = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return ChatResult(
            content=json.dumps(payload, ensure_ascii=False),
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


def _state() -> GameState:
    card = make_card()
    return GameState(
        session_id="ses_narrate",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=1, supplies=1),
        characters={"chr_a": CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)},
    )


def _agent(provider: Canned) -> AgentLLM:
    cfg = LLMConfig(ROOT)
    cfg._adapters = {"minimax": provider}
    return AgentLLM(cfg, {"chr_a": make_card()}, seeds=PACK.seeds, seed="42")


def _judged(state: GameState) -> tuple[PlayerAction, object]:
    card = make_card()
    action = PlayerAction(
        actor_id="chr_a",
        thought="先别动。",
        action="用撬锁试探锁孔",
        approach="贴着墙",
        skill="撬锁",
        intent=Intent.careful,
    )
    return action, judge(action, card, state, Rules(ROOT), DiceEngine("t1"))


def test_narrate_batch_returns_loot_and_no_enemies():
    state = _state()
    action, result = _judged(state)
    provider = Canned(
        [
            {
                "narrations": [{"actor_id": "chr_a", "text": "锁芯松了。"}],
                "narration_meta": {"beat": "rising"},
                "loot": [
                    {"name": "磨损的撬锁工具组", "slot": "tool", "tags": ["lockpick"], "owner": "chr_a"}
                ],
                "state_delta": {"turn": 1, "changes": []},
            }
        ]
    )
    out = _agent(provider).narrate_batch(state, [action], [result], 1)

    assert set(out) == {"narrations", "narration_meta", "state_delta", "npc_moves", "loot"}
    assert out["loot"] and out["loot"][0]["name"] == "磨损的撬锁工具组"
    assert "enemies" not in out, "NARRATE 不再申报敌人，敌人唯一入口是 SCENE"


def test_narrate_prompt_has_no_enemy_declaration_but_scene_does():
    state = _state()
    action, result = _judged(state)
    provider = Canned(
        [
            {"narration": "巷子尽头有一盏灭了的灯。", "hooks": [], "present_npcs": [], "enemies": []},
            {
                "narrations": [{"actor_id": "chr_a", "text": "锁芯松了。"}],
                "state_delta": None,
            },
        ]
    )
    agent = _agent(provider)
    agent.describe_scene(state, 1)
    agent.narrate_batch(state, [action], [result], 1)

    scene_prompt = json.dumps(provider.calls[0], ensure_ascii=False)
    narrate_prompt = json.dumps(provider.calls[1], ensure_ascii=False)
    assert "enemies" in scene_prompt, "SCENE 仍是敌人申报入口（C25）"
    assert "必须用 `enemies` 申报" not in narrate_prompt
    assert "敌人不在这一步申报" in narrate_prompt


class LootLLM(MockLLM):
    """叙事阶段申报战利品 + 一个本不该生效的敌人。"""

    def narrate_batch(self, state, actions, results, turn, npc_moves=None):
        owner = actions[0].actor_id
        return {
            "narrations": {a.actor_id: f"{a.actor_id} 拿到了东西。" for a in actions},
            "narration_meta": {},
            "state_delta": None,
            "npc_moves": [],
            "loot": [{"name": "旧怀表", "slot": "trinket", "tags": ["heirloom"], "owner": owner}],
            "enemies": [{"id": "en_late", "name": "晚到的家伙", "threat": "weak"}],
        }


def _loop(llm) -> GameLoop:
    cards = {f"chr_p{i + 1}": make_card(profession=["盗贼", "战士", "学者"][i]) for i in range(2)}
    state = GameState(
        session_id="ses_narrate_loop",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=10, supplies=3),
        characters={
            cid: CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)
            for cid, card in cards.items()
        },
    )
    return GameLoop(Rules(ROOT), cards, state, llm, seed=42)


def test_loot_lands_in_inventory_and_late_enemy_is_ignored():
    loop = _loop(LootLLM(seed=3))
    rec = asyncio.run(loop.run(1))[0]

    assert rec.loot, "GM 申报的战利品必须真的入包（B22）"
    owner = rec.loot[0]["owner"]
    assert rec.loot[0]["id"] in loop.state.characters[owner].inventory
    assert "en_late" not in loop.state.enemies, "NARRATE 阶段的敌人申报不再被消费（C25 唯一入口）"
