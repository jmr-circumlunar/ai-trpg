"""会话组装：CLI 与 API 共用同一套「怎么开一局」的逻辑。"""

from __future__ import annotations

import uuid
from pathlib import Path

from .characters import preset_cards
from .llm import AgentLLM, LLMConfig, MockLLM
from .models import CharacterRuntime, GameState, Party, Scene, WorldRef
from .rules.loader import Rules
from .world import WorldPack

def build_cards(players: int, pack=None) -> dict:
    """预设小队：职业来自世界包（没有则用内置模板）。"""
    return preset_cards(players, pack=pack)


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


def seed_lore(vectors, pack) -> int:
    """把世界包的 archive 长文写进公共向量桶（不常驻 prompt）。"""
    docs = getattr(pack, "archive_docs", None) or []
    if not docs or vectors is None:
        return 0
    try:
        return vectors.add_many(
            "_lore",
            [str(d.get("text") or "") for d in docs],
            kind="lore",
            ref_ids=[str(d.get("path") or "") for d in docs],
        )
    except Exception:  # noqa: BLE001
        return 0


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
        seeds=pack.seeds,
        seed=seed,
        world_core=pack.core_text,
    )
