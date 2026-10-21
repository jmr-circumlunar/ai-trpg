"""角色卡的生产侧定义（区别于 testing.py 的测试构造器）。

一期目标：角色可新建/编辑/删除，字段由页面表单驱动。
职业模板只提供默认值，所有字段都可被表单覆盖。
"""

from __future__ import annotations

from .models import AIExtended, CardData, CardExtensions, CharacterCard, ModelConfig

DEFAULT_STATS = {"str": 10, "dex": 10, "int": 10, "wis": 10, "cha": 10}

# 职业模板：默认属性倾向、技能、出身描述（全部可被表单覆盖）
PROFESSION_TEMPLATES: dict[str, dict] = {
    "盗贼": {
        "description": "港区的撬锁人，左手少了两指",
        "personality": "话少、不信人、对货物有执念",
        "private_goal": "找回被行会没收的妹妹遗物",
        "stats": {"str": 9, "dex": 15, "int": 12, "wis": 11, "cha": 8},
        "skills": {"撬锁": 3, "潜行": 2, "观察": 1},
    },
    "战士": {
        "description": "退役的夜巡老兵，右耳听不见",
        "personality": "直、护短、不肯认错",
        "private_goal": "查清三年前那场火灾是谁下的令",
        "stats": {"str": 15, "dex": 11, "int": 9, "wis": 12, "cha": 10},
        "skills": {"威吓": 2, "攀爬": 2, "器械": 1},
    },
    "学者": {
        "description": "替教会抄书的年轻人，眼睛不好",
        "personality": "好奇、迂腐、记性极好",
        "private_goal": "读到禁书区最里面那一本",
        "stats": {"str": 8, "dex": 10, "int": 16, "wis": 13, "cha": 11},
        "skills": {"学识": 3, "观察": 2, "医疗": 1},
    },
    "医师": {
        "description": "走街的游医，箱子比人还重",
        "personality": "温和、怕血、嘴上不饶人",
        "private_goal": "找到消失了两年的师父",
        "stats": {"str": 9, "dex": 12, "int": 13, "wis": 15, "cha": 11},
        "skills": {"医疗": 3, "交涉": 2, "观察": 1},
    },
    "斥候": {
        "description": "给商队带路的外乡人",
        "personality": "警觉、爱开玩笑、随时准备跑",
        "private_goal": "攒够钱离开这座城",
        "stats": {"str": 11, "dex": 15, "int": 10, "wis": 13, "cha": 9},
        "skills": {"潜行": 2, "追踪": 2, "生存": 2},
    },
    "术士": {
        "description": "被同行称作骗子的符术师",
        "personality": "浮夸、精于算计、真有点本事",
        "private_goal": "证明自己不是骗子",
        "stats": {"str": 8, "dex": 11, "int": 15, "wis": 14, "cha": 14},
        "skills": {"学识": 2, "意志": 3, "交涉": 1},
    },
}

PROFESSIONS = list(PROFESSION_TEMPLATES)


def new_card(
    *,
    name: str,
    profession: str = "盗贼",
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
    tpl = PROFESSION_TEMPLATES.get(profession) or PROFESSION_TEMPLATES["盗贼"]
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
            description=description if description is not None else tpl["description"],
            personality=personality if personality is not None else tpl["personality"],
            tags=[profession],
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
                        private_goal if private_goal is not None else tpl["private_goal"]
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


def preset_cards(players: int) -> dict[str, CharacterCard]:
    """按职业轮转生成预设小队（一期默认队伍，页面可再编辑）。"""
    cards: dict[str, CharacterCard] = {}
    for i in range(players):
        profession = PROFESSIONS[i % len(PROFESSIONS)]
        cid = f"chr_p{i + 1}"
        cards[cid] = new_card(
            name=f"角色{i + 1}（{profession}）", profession=profession
        )
    return cards
