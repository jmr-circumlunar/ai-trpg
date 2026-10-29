"""角色卡的生产侧定义（区别于 tests/support/builders.py 的测试构造器）。

两条生产线：
- **三层职业**（世界包有 `origins.yaml`）：`new_card_by_origin` 按权重抽出身、抽天赋、
  默认身份，人格与私人目标来自世界包数据（不再是空白）。
- **单层职业**（旧世界，如 `ironvale`）：`new_card` 沿用 `professions.yaml`。

两条线共用同一个 `CharacterCard` 结构（数据契约 §二），差别只在字段来源。

doc: 03-模块/M10-成长/00-模块总述.md
module: M10
layer: L3
"""

from __future__ import annotations

import random
from pathlib import Path

from ..equipment import roll as eq
from ..models import AIExtended, CardData, CardExtensions, CharacterCard, ModelConfig
from ..models.rng import derive
from . import identity
from ..world import WorldPackError

DEFAULT_STATS = {"str": 10, "dex": 10, "int": 10, "wis": 10, "cha": 10}

#: 仓库根（`trpg/growth/build.py` → 上两级）。人格库固定在根 `config/`。
_REPO_ROOT = Path(__file__).resolve().parents[2]


def templates_from_pack(pack) -> dict[str, dict]:
    """职业模板只能来自世界包（代码里不藏任何世界内容）。"""
    if pack is None or not getattr(pack, "professions", None):
        return {}
    return {str(p.get("id")): p for p in pack.professions}


def new_card(
    *,
    name: str,
    profession: str = "",
    pack=None,
    description: str | None = None,
    personality: str | None = None,
    private_goal: str | None = None,
    fear: str = "",
    bottom_line: str = "",
    origin_region: str | None = None,
    stats: dict[str, int] | None = None,
    skills: dict[str, int] | None = None,
    provider: str = "",
    model: str = "",
    reasoning_effort: str = "",
    level: int = 1,
    hp_max: int = 11,
    sanity_max: int = 10,
    equipment: list | None = None,
) -> CharacterCard:
    pool = templates_from_pack(pack)
    # 没有对应模板就建空白卡：不拿任何世界的内容兜底（默认值不许藏世界）
    tpl = pool.get(profession) or {}
    final_stats = dict(DEFAULT_STATS)
    final_stats.update(tpl.get("stats") or {})
    if stats:
        final_stats.update({k: int(v) for k, v in stats.items()})

    final_skills = dict(tpl.get("skills") or {})
    if skills:
        final_skills.update({k: int(v) for k, v in skills.items()})

    return CharacterCard(
        spec="chara_card_v2",
        spec_version="2.0",
        data=CardData(
            name=name,
            description=(
                description if description is not None else str(tpl.get("description") or "")
            ),
            personality=(
                personality if personality is not None else str(tpl.get("personality") or "")
            ),
            tags=[profession] if profession else [],
            extensions=CardExtensions(
                ai_trpg=AIExtended(
                    profession=profession,
                    origin_region=origin_region,
                    level=level,
                    stats=final_stats,
                    hp=hp_max,
                    hp_max=hp_max,
                    sanity=sanity_max,
                    sanity_max=sanity_max,
                    skills=final_skills,
                    private_goal=(
                        private_goal
                        if private_goal is not None
                        else str(tpl.get("private_goal") or "")
                    ),
                    fear=fear,
                    bottom_line=bottom_line,
                    equipment=list(equipment or []),
                    model=ModelConfig(
                        provider=provider,
                        model=model,
                        reasoning_effort=reasoning_effort,
                    ),
                )
            ),
        ),
    )


# ── 三层职业（出身 / 天赋 / 身份）────────────────────────────
def starter_equipment(pack, origin_id: str = "", owner_id: str = "") -> list:
    """报到初始装备：世界包 `items.yaml: starter`（default + by_origin）。"""
    if pack is None:
        return []
    out = []
    for tid in pack.starter_items(origin_id):
        tpl = pack.item_template(tid)
        if tpl is None:
            continue
        out.append(eq.instance_from_template(tpl, owner_id=owner_id or None))
    return out


def new_card_by_origin(
    *,
    name: str,
    pack,
    origin_id: str = "",
    rng: random.Random | None = None,
    path: str = "",
    description: str | None = None,
    personality: str | None = None,
    private_goal: str | None = None,
    fear: str = "",
    bottom_line: str = "",
    origin_region: str | None = None,
    stats: dict[str, int] | None = None,
    skills: dict[str, int] | None = None,
    provider: str = "",
    model: str = "",
    reasoning_effort: str = "",
    level: int = 1,
    hp_max: int = 11,
    sanity_max: int = 10,
    equipment: list | None = None,
) -> CharacterCard:
    """按出身建卡：抽出身（或指定）→ 叠加属性 → 抽天赋 → 取默认身份 → 发初始装备。"""
    if pack is None or not getattr(pack, "has_origins", False):
        raise WorldPackError("该世界没有 origins.yaml，请用 new_card()（单层职业）")

    rng = rng or random.Random()
    origin = pack.origin(origin_id) if origin_id else identity.roll_origin(pack, rng)
    if origin is None:
        raise WorldPackError(
            f"世界 {pack.world_id} 没有出身 '{origin_id}'（可用：{', '.join(o['id'] for o in pack.origins)}）"
        )

    rolled = identity.roll_talents(pack, rng)
    born = [{"label": t} for t in (origin.get("talents") or [])]
    talents = identity.talent_labels(born + rolled)

    final_stats = identity.origin_stats(origin)
    if stats:
        final_stats.update({k: int(v) for k, v in stats.items()})

    final_skills = identity.talent_skill_bonus(rolled)
    if skills:
        final_skills.update({k: int(v) for k, v in skills.items()})

    goals = list(origin.get("private_goals") or [])
    final_goal = private_goal if private_goal is not None else (rng.choice(goals) if goals else "")

    path_id = path or pack.path_default()
    tags = [
        str(origin.get("label") or origin.get("id")),
        pack.path_label(path_id) if path_id else "",
        *(origin.get("tags") or []),
    ]
    tags = [t for t in tags if t]

    final_equipment = (
        list(equipment) if equipment is not None else starter_equipment(pack, str(origin.get("id")), name)
    )

    return CharacterCard(
        spec="chara_card_v2",
        spec_version="2.0",
        data=CardData(
            name=name,
            description=description if description is not None else str(origin.get("description") or ""),
            personality=personality if personality is not None else str(origin.get("personality") or ""),
            tags=tags,
            extensions=CardExtensions(
                ai_trpg=AIExtended(
                    profession=str(origin.get("label") or origin.get("id")),
                    origin=str(origin.get("id") or ""),
                    path=path_id,
                    talents=talents,
                    origin_region=origin_region or origin.get("region"),
                    level=level,
                    stats=final_stats,
                    hp=hp_max,
                    hp_max=hp_max,
                    sanity=sanity_max,
                    sanity_max=sanity_max,
                    skills=final_skills,
                    private_goal=final_goal,
                    fear=fear,
                    bottom_line=bottom_line,
                    equipment=final_equipment,
                    model=ModelConfig(
                        provider=provider,
                        model=model,
                        reasoning_effort=reasoning_effort,
                    ),
                )
            ),
        ),
    )


def card_from_form(
    pack,
    *,
    origin_id: str = "",
    path_id: str = "",
    profession: str = "",
    **kw,
) -> CharacterCard:
    """表单/接口入口：三层世界按出身建卡，单层世界按职业建卡。

    「世界形态差异」只允许出现在两处：这里是其一，另一处是 `world/pack.py` 的能力查询。
    其它任何地方都不许写 `if pack.has_origins`。
    """
    if pack is not None and getattr(pack, "has_origins", False):
        return new_card_by_origin(pack=pack, origin_id=origin_id, path=path_id, **kw)
    return new_card(pack=pack, profession=profession, **kw)


def preset_cards(players: int, pack=None, seed: str | int | None = None) -> dict[str, CharacterCard]:
    """预设小队：有 origins 走出身逻辑（同 seed 同队伍），否则按职业池发牌。

    **人格与身份分离**（2026-10-26）：
    - 人格来自通用库 `config/agents.yaml`（全局一套，与身份无关），按种子取前 N 个；
    - 身份从职业池**随机发**（同 seed 同结果）。

    以前身份是按池子顺序轮转的（狼/狼/狼/预言家…），等于每局座位固定；
    名字则是「角色N（职业）」，人格来自职业牌——同一个人这局是什么身份、
    上局是什么性格，全都不稳定。现在人还是那个人，牌每局重抽。
    """
    roster = _agent_roster(players, pack, seed)

    if pack is not None and getattr(pack, "has_origins", False):
        rng = derive(seed, "cards") if seed is not None else random.Random()
        cards: dict[str, CharacterCard] = {}
        for i in range(players):
            origin = identity.roll_origin(pack, rng)
            label = str((origin or {}).get("label") or "角色")
            agent = roster[i] if i < len(roster) else {}
            cards[f"chr_p{i + 1}"] = new_card_by_origin(
                name=str(agent.get("name") or f"角色{i + 1}（{label}）"),
                pack=pack,
                origin_id=str((origin or {}).get("id") or ""),
                rng=rng,
                personality=_persona_text(agent) or None,
            )
        return cards

    # 职业池只来自世界包；池空 = 建空白卡（不拿代码里的内容兜底）
    pool = [str(p.get("id")) for p in (pack.profession_pool() if pack else []) if p.get("id")]
    # 先按人数取牌组，再打乱顺序：
    # 池子本身就是「3 狼 + 3 神 + 2 民」的牌堆，取前 N 张保证每局的**构成**正确
    # （4 人局不会发出 4 张狼牌），打乱只影响「谁坐哪张牌」。
    roles = list(pool)[:players] if pool else []
    if len(roles) > 1:
        rng = derive(seed, "roles") if seed is not None else random.Random()
        rng.shuffle(roles)

    cards = {}
    for i in range(players):
        profession = roles[i % len(roles)] if roles else ""
        cid = f"chr_p{i + 1}"
        agent = roster[i] if i < len(roster) else {}
        cards[cid] = new_card(
            name=str(agent.get("name") or f"角色{i + 1}（{profession or '角色'}）"),
            profession=profession,
            pack=pack,
            personality=_persona_text(agent) or None,
        )
    return cards


def _agent_roster(players: int, pack, seed: str | int | None) -> list[dict]:
    """通用人格库（`config/agents.yaml`）：取前 N 个，`agents_random` 时按种子打乱。

    **库在仓库根 `config/`，不是世界包目录**：`pack.root` 指向 `worlds/<id>`，
    拿它去拼 `config/agents.yaml` 永远读不到（这就是第一版没生效的原因）。
    配置文件缺失时返回空列表，调用方回落到旧的命名方式。
    """
    from ..config.loader import config_path, load_yaml

    try:
        doc = load_yaml(config_path(_REPO_ROOT, "agents"), {}) or {}
    except Exception:  # noqa: BLE001
        return []
    pool = [dict(item) for item in (doc.get("agents") or []) if item.get("name")]
    if not pool:
        return []
    if bool(doc.get("agents_random", True)) and seed is not None:
        derive(seed, "agents").shuffle(pool)
    return pool[:max(0, players)] if players else pool


def _persona_text(agent: dict) -> str:
    """把人格条目压成一句进 `card.data.personality`（prompt 已经会读这个字段）。"""
    if not agent:
        return ""
    traits = "、".join(str(item) for item in (agent.get("traits") or []) if item)
    persona = str(agent.get("persona") or "").strip()
    voice = str(agent.get("voice") or "").strip()
    head = f"{traits}：" if traits else ""
    tail = f"（说话风格：{voice}）" if voice else ""
    return f"{head}{persona}{tail}".strip()
