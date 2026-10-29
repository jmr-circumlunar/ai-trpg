"""会话组装：CLI 与 API 共用同一套「怎么开一局」的逻辑。"""

from __future__ import annotations

import uuid
from pathlib import Path

from .growth.build import preset_cards
from .agents import AgentLLM, LLMConfig, MockLLM
from .models import CharacterRuntime, Clock, Exit, GameState, Party, Scene, WorldRef
from .map import exits_for, generate_region_map
from .rules.loader import Rules
from .world import WorldPack


# 开局资源（P3 起按人数均分：余数进公共仓）
_START_GOLD = 30
_START_SUPPLIES = 4


def build_cards(players: int, pack=None, seed: str | int | None = None) -> dict:
    """预设小队：有 origins 按出身抽（同 seed 同队伍），否则按职业池轮转。"""
    return preset_cards(players, pack=pack, seed=seed)


def build_state(
    pack: WorldPack,
    cards: dict,
    era: str | None = None,
    timeline: str | None = None,
    world_day: int = 1,
    seed: str = "42",
    region_map: dict | None = None,
    rules_map: dict | None = None,
) -> GameState:
    """开一局。

    `timeline` 为空时取 world.yaml 的 default_timeline；era 优先由 timeline 决定
    （二期规则：时间点携带技术时代，显式 era 只在没有 timeline 时生效）。
    """
    origins = [c.ext.origin_region for c in cards.values() if c.ext.origin_region]
    region = pack.starter_region(origins)

    point = pack.timeline_point(timeline or pack.default_timeline) or {}
    timeline_id = str(point.get("id") or timeline or pack.default_timeline)
    era_id = str(point.get("era") or era or pack.default_era)

    # 地图：开局生成一次（纯函数，同 seed 同图）；调用方可传入已落库的那张
    entry = pack.region_entry(region)
    if region_map is None:
        region_map = generate_region_map(pack, region, seed, rules_map)
    exits = [Exit(**item) for item in exits_for(region_map, entry)]

    return GameState(
        session_id=f"ses_{uuid.uuid4().hex[:6]}",
        world=WorldRef(world_id=pack.world_id, era=era_id, timeline=timeline_id),
        # 开局时间取该时间点的 start_clock（如"报到日 08:00"），不是模型默认值；
        # world_day > 1 时把日期往后推（同世界连续多局）
        clock=Clock(
            **{
                **_shifted_clock(pack.start_clock(timeline_id), world_day),
                # 开局日期来自世界包日历；没有日历的世界留空，显示回退「第 N 天」
                "start_date": pack.calendar_start,
            }
        ),
        scene=Scene(
            # 落地地点取 regions.<id>.entry，必须是地点表里真实存在的 id
            location_id=entry,
            region=region,
            description=_opening_text(pack, region, timeline_id),
            # 出口由代码按地图填（P2.5-T4 起 GM 不能再写 scene.exits）
            exits=exits,
        ),
        # 开局钱财按人数均分给每个角色，余数留公共仓：
        # 每个 Agent 是一个人、有自己的钱；`party` 只留凑钱办事的公用部分
        party=Party(gold=_START_GOLD % max(1, len(cards)),
                    supplies=_START_SUPPLIES % max(1, len(cards))),
        characters={
            cid: CharacterRuntime(
                hp=card.ext.hp,
                sanity=card.ext.sanity,
                path=card.ext.path,
                # 阵营来自世界包职业牌（`professions.yaml: camp`）；没有就是空
                camp=_profession_camp(pack, card),
                gold=_START_GOLD // max(1, len(cards)),
                supplies=_START_SUPPLIES // max(1, len(cards)),
            )
            for cid, card in cards.items()
        },
    )


def _shifted_clock(start: dict, world_day: int) -> dict:
    """按世界第几天平移开局时间（第一天不动）。"""
    out = dict(start or {})
    if int(world_day or 1) > 1:
        try:
            out["day"] = int(out.get("day") or 1) + int(world_day) - 1
        except (TypeError, ValueError):
            pass
    return out


def _profession_camp(pack, card) -> str:
    """职业牌上声明的阵营（C56）：世界包说了才算，传统世界恒为空。"""
    profession = str(getattr(card.ext, "profession", "") or "")
    if not profession:
        return ""
    for item in getattr(pack, "professions", None) or []:
        if str(item.get("id")) == profession:
            return str(item.get("camp") or "")
    return ""


def _opening_text(pack: WorldPack, region: str, timeline: str = "") -> str:
    """开场描述：优先时间点的 premise，其次世界基调，最后给一句保底。"""
    point = pack.timeline_point(timeline or pack.default_timeline) or {}
    premise = str(point.get("premise") or "").strip()
    if premise:
        return premise
    return (pack.tone or "").strip()[:120] or f"{pack.name}的{region}"


def build_memory(
    root: Path,
    provider: str = "mock",
    conn=None,
    session_id: str = "",
):
    """构造向量存储：MiniMax 走真 embedding，其余情况降级为离线哈希。

    降级不是"偷懒"：断网或换供应商时链路仍可跑，测试也不会依赖外部服务。
    """
    from .agents import LLMConfig
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


def hydrate_llm(llm, conn, session_id: str, recent_turns: int = 3, keep_chapters: int = 8) -> None:
    """读档/回滚后回灌短期上下文与章节摘要（B25）。

    重建出来的 `AgentLLM` 默认是空白上下文——不灌回去就等于「队伍刚睁眼」，
    所以 `play.py`（读档）与 `sessions.py`（回滚）必须在建完 llm 后调这里。
    mock 局（`MockLLM`）没有这两个字段，静默跳过。
    """
    hook = getattr(llm, "hydrate", None)
    if not callable(hook) or conn is None:
        return
    from .storage import db as _db

    try:
        hook(
            recent_public=_db.recent_narrations(
                conn, session_id, limit=max(4, int(recent_turns) * 3)
            ),
            summaries=_db.load_summaries(conn, session_id, limit=keep_chapters),
        )
    except Exception:  # noqa: BLE001  回灌失败不应拦住读档
        pass


def build_director(
    root: Path,
    rules: Rules,
    pack: WorldPack,
    cards: dict,
    provider: str = "mock",
    seed: str | int = 42,
    model: str | None = None,
    reasoning_effort: str | None = None,
    style: str | None = None,
):
    """导演：mock 走纯规则（可复现），真实 provider 让模型设计主线。"""
    from .agents import Director, LLMDirector

    if provider == "mock":
        return Director(pack=pack, seed=seed)
    try:
        cfg = LLMConfig(root)
        return LLMDirector(
            cfg,
            pack=pack,
            seed=seed,
            provider=None if provider == "auto" else provider,
            model=model,
            reasoning_effort=reasoning_effort,
            world_name=pack.name,
            style=style or pack.style,
            taboos=pack.taboos,
        )
    except Exception:  # noqa: BLE001
        return Director(pack=pack, seed=seed)


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
    style: str | None = None,
    legacy: str = "",
):
    if provider == "mock":
        return MockLLM(seed)

    config = LLMConfig(root)
    return AgentLLM(
        config,
        cards,
        skills=list(rules.skill_attr.keys()),
        taboos=pack.taboos,
        style=style or pack.style,
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
        world_orgs=pack.organizations_text(),
        pack=pack,
        legacy=legacy,
    )
