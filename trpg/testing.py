"""测试与 demo 用的构造器（不放生产逻辑）。"""

from __future__ import annotations

from .models import (
    CharacterCard,
    CharacterRuntime,
    GameState,
    Party,
    Scene,
    WorldRef,
)


def make_card(cid: str = "chr_test01", hp: int = 11, profession: str = "盗贼") -> CharacterCard:
    return CharacterCard.model_validate(
        {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": "测试者",
                "description": "用于测试的角色",
                "personality": "话少",
                "tags": ["测试"],
                "extensions": {
                    "ai_trpg": {
                        "profession": profession,
                        "affiliation": "测试组",
                        "origin_region": "north_port",
                        "level": 2,
                        "xp": 100,
                        "stats": {"str": 9, "dex": 15, "int": 12, "wis": 11, "cha": 8},
                        "hp": hp,
                        "hp_max": 11,
                        "sanity": 10,
                        "sanity_max": 10,
                        "skills": {"撬锁": 2, "潜行": 1},
                    }
                },
            },
        }
    )


def make_state(card: CharacterCard | None = None, session_id: str = "ses_test01") -> GameState:
    card = card or make_card()
    return GameState(
        session_id=session_id,
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port", description="测试场景"),
        party=Party(gold=42, supplies=5),
        characters={"chr_test01": CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)},
    )
