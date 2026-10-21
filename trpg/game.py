"""会话组装：CLI 与 API 共用同一套「怎么开一局」的逻辑。"""

from __future__ import annotations

import uuid
from pathlib import Path

from .llm import AgentLLM, LLMConfig, MockLLM
from .models import CharacterRuntime, GameState, Party, Scene, WorldRef
from .rules.loader import Rules
from .testing import make_card
from .world import WorldPack

PROFESSIONS = ["盗贼", "战士", "学者", "医师", "斥候", "术士"]

# 目标互相冲突，AI 才不会全员配合
PERSONAS = {
    "盗贼": ("港区的撬锁人，左手少了两指", "话少、不信人、对货物有执念", "找回被行会没收的妹妹遗物"),
    "战士": ("退役的夜巡老兵，右耳听不见", "直、护短、不肯认错", "查清三年前那场火灾是谁下的令"),
    "学者": ("替教会抄书的年轻人，眼睛不好", "好奇、迂腐、记性极好", "读到禁书区最里面那一本"),
    "医师": ("走街的游医，箱子比人还重", "温和、怕血、嘴上不饶人", "找到消失了两年的师父"),
    "斥候": ("给商队带路的外乡人", "警觉、爱开玩笑、随时准备跑", "攒够钱离开这座城"),
    "术士": ("被同行称作骗子的符术师", "浮夸、精于算计、真有点本事", "证明自己不是骗子"),
}


def build_cards(players: int) -> dict:
    cards = {}
    for i in range(players):
        cid = f"chr_p{i + 1}"
        profession = PROFESSIONS[i % len(PROFESSIONS)]
        card = make_card(profession=profession)
        desc, persona, goal = PERSONAS[profession]
        card.data.name = f"角色{i + 1}（{profession}）"
        card.data.description = desc
        card.data.personality = persona
        card.ext.private_goal = goal
        cards[cid] = card
    return cards


def build_state(pack: WorldPack, cards: dict, era: str | None = None) -> GameState:
    origins = [c.ext.origin_region for c in cards.values() if c.ext.origin_region]
    region = pack.starter_region(origins)
    return GameState(
        session_id=f"ses_{uuid.uuid4().hex[:6]}",
        world=WorldRef(world_id=pack.world_id, era=era or pack.default_era),
        scene=Scene(
            location_id=f"loc_{region}",
            region=region,
            description=pack.tone[:120] or f"{pack.name}的{region}",
        ),
        party=Party(gold=30, supplies=4),
        characters={
            cid: CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)
            for cid, card in cards.items()
        },
    )


def build_memory(
    root: Path,
    provider: str = "mock",
    conn=None,
    session_id: str = "",
):
    """构造向量存储：MiniMax 走真 embedding，其余情况降级为离线哈希。

    降级不是"偷懒"：断网或换供应商时链路仍可跑，测试也不会依赖外部服务。
    """
    from .llm import LLMConfig
    from .memory import HashEmbedder, MiniMaxEmbedder, VectorStore

    if provider == "mock":
        return VectorStore(conn, session_id, HashEmbedder())

    try:
        cfg = LLMConfig(root)
        name = provider if provider != "auto" else str(cfg.defaults.get("provider", "minimax"))
        pcfg = cfg.providers.get(name) or {}
        key = cfg.api_key(name)
        base = pcfg.get("base_url") or ""
        if pcfg.get("kind") == "minimax" and key and base:
            return VectorStore(conn, session_id, MiniMaxEmbedder(key, base))
    except Exception:  # noqa: BLE001
        pass
    return VectorStore(conn, session_id, HashEmbedder())


def build_llm(
    root: Path,
    rules: Rules,
    pack: WorldPack,
    cards: dict,
    provider: str = "mock",
    model: str | None = None,
    seed: str | int = 42,
    reasoning_effort: str | None = None,
    events=None,
    vectors=None,
):
    if provider == "mock":
        return MockLLM(seed)

    config = LLMConfig(root)
    return AgentLLM(
        config,
        cards,
        skills=list(rules.skill_attr.keys()),
        taboos=pack.taboos,
        style=pack.style,
        world_name=pack.name,
        provider=None if provider == "auto" else provider,
        model=model,
        reasoning_effort=reasoning_effort,
        rules=rules,
        events=events,
        npcs=pack.npcs,
        vectors=vectors,
    )
