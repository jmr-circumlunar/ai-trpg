"""W1 · 狼人杀专项回归（夜间侧）：夜间不可见 / 遗言 / 猎人开枪 / 女巫双药。

被测对象是**真实世界包** `worlds/werewolf/` + **真实框架**；
每条断言都对应一条**标准规则**（对照 2026-10-30 联网核对的规则表，见
知识库 `过程记录/方案/41-狼人杀专项回归套件-W1`）：

| 规则 | 本文件的断言 |
|---|---|
| R2 遗言：首夜有、之后夜里没有、被投始终有 | `test_first_night_death_has_last_words` / `test_second_night_death_has_no_last_words` / `test_exiled_player_always_has_last_words` |
| R3 女巫：一晚一瓶、不可自救 | `test_witch_uses_only_one_potion_per_night` / `test_witch_cannot_heal_herself` |
| R4 猎人：被投可开枪、被毒不可开枪 | `test_hunter_shoots_when_exiled` / `test_hunter_cannot_shoot_when_poisoned` |
| R6 夜间私密 | `test_night_council_is_invisible_to_non_wolves` / `test_seer_check_is_private` |
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.werewolf_board import (  # noqa: E402
    GODS,
    VILLAGERS,
    WOLVES,
    build_board,
    causes,
    events_of,
    run_turns,
    votes,
)
from trpg.models.enums import Position  # noqa: E402

# 夜里狼的共同决定 / 组内商议：这两个词一旦出现在非狼人的信息里就是泄露
NIGHT_MARKERS = ("共同决定", "商议")


# ── R6 夜间对非狼人不可见 ─────────────────────────────────────


def test_night_council_is_invisible_to_non_wolves():
    """狼内部商量与共同决定，只有狼看得到。"""
    loop, llm = build_board(vote_plan=votes(1, council="chr_7"))

    records, events = run_turns(loop, 1)

    assert records[0].order[0] == "night", "夜间必须是第一个阶段"
    wolf_briefs = [line for cid in WOLVES for line in llm.all_briefs(cid)]
    assert any("共同决定" in line for line in wolf_briefs), "狼组应当看到夜里定下的目标"
    for cid in ("chr_4", "chr_5", "chr_6", "chr_7", "chr_8"):
        leaked = [
            line
            for line in llm.all_briefs(cid)
            if any(marker in line for marker in NIGHT_MARKERS)
        ]
        assert not leaked, f"{cid} 不该看到夜里的商议：{leaked}"


def test_night_council_event_only_targets_the_wolves():
    """共议事件的观众名单就是狼——事件出口也不能泄给非狼人。"""
    loop, _ = build_board(vote_plan=votes(1, council="chr_7"))

    _records, events = run_turns(loop, 1)

    councils = events_of(events, "council")
    assert councils, "夜里应当有共议事件"
    for payload in councils:
        assert set(payload.get("audience") or []) == set(WOLVES)


def test_seer_check_is_private():
    """查验结果只进本人笔记（公开写出去等于自曝预言家）。"""
    loop, llm = build_board(
        skill_plan={("chr_4", 1): {"skill": "查验", "target": "chr_1"}},
        vote_plan=votes(1, council="chr_7"),
    )

    records, events = run_turns(loop, 1)

    notes = loop.state.characters["chr_4"].notes
    assert notes and "阵营是" in notes[-1], "预言家应当拿到查验结果"
    assert "wolf" in notes[-1], "chr_1 是狼人"
    for cid, runtime in loop.state.characters.items():
        if cid == "chr_4":
            continue
        assert not runtime.notes, f"{cid} 不该拿到查验结果"
        assert all(
            "阵营是" not in line for line in llm.all_briefs(cid)
        ), f"{cid} 不该看到查验结果"
    # 私密技能的观众只有本人
    checks = [item for item in events_of(events, "skill") if item.get("effect") == "reveal_camp"]
    assert checks and checks[0].get("audience") == ["chr_4"]


# ── R2 遗言 ───────────────────────────────────────────────────


def test_first_night_death_has_last_words():
    """标准规则：首夜夜里出局的人有遗言。"""
    loop, llm = build_board(vote_plan=votes(1, council="chr_7"))

    records, _ = run_turns(loop, 1)

    rec = records[0]
    assert causes(rec) == ["consensus_result"], "夜里应当有人出局"
    death = rec.deaths[0]
    assert death["id"] == "chr_7"
    assert death.get("last_words"), "首夜出局应当有遗言"
    assert ("chr_7", 1) in llm.last_words_calls


def test_second_night_death_has_no_last_words():
    """标准规则：第二夜起夜里出局静默（`last_words_when: first_turn`）。"""
    loop, llm = build_board(
        vote_plan={**votes(1, council="chr_7"), **votes(2, council="chr_8")}
    )

    records, _ = run_turns(loop, 2)

    night_two = records[1]
    assert causes(night_two) == ["consensus_result"]
    assert "last_words" not in night_two.deaths[0], "第二夜出局不该有遗言"
    assert ("chr_8", 2) not in llm.last_words_calls


def test_exiled_player_always_has_last_words():
    """标准规则：被投票放逐的人**始终**有遗言（不分第几夜）。"""
    loop, llm = build_board(
        vote_plan=votes(
            1,
            council="chr_7",
            day_rounds=(
                {
                    "chr_1": "chr_8",
                    "chr_2": "chr_8",
                    "chr_3": "chr_8",
                    "chr_4": "chr_8",
                    "chr_5": "chr_8",
                    "chr_6": "chr_8",
                    "chr_8": None,
                },
            ),
        )
    )

    records, _ = run_turns(loop, 1)

    rec = records[0]
    exiled = next(item for item in rec.deaths if item.get("cause") == "collective_result")
    assert exiled["id"] == "chr_8"
    assert exiled.get("last_words"), "被放逐者应当有遗言"
    assert ("chr_8", 1) in llm.last_words_calls


# ── R4 猎人开枪 ───────────────────────────────────────────────


def test_hunter_shoots_when_exiled():
    """标准规则：猎人被投票放逐时可以开枪带走一人。"""
    loop, _ = build_board(
        skill_plan={("chr_6", 1): {"skill": "开枪", "target": "chr_1"}},
        vote_plan=votes(
            1,
            council="chr_7",
            day_rounds=(
                {
                    "chr_1": "chr_6",
                    "chr_2": "chr_6",
                    "chr_3": "chr_6",
                    "chr_4": "chr_6",
                    "chr_5": "chr_6",
                    "chr_8": "chr_6",
                    "chr_6": None,
                },
            ),
        ),
    )

    records, _ = run_turns(loop, 1)

    rec = records[0]
    assert "collective_result" in causes(rec), "猎人应当被投出局"
    assert "shoot" in causes(rec), "猎人出局应当开枪"
    shot = next(item for item in rec.deaths if item.get("cause") == "shoot")
    assert shot["id"] == "chr_1"
    # 显示文案由世界包给（框架不认识「被开枪带走」）
    assert shot.get("cause_label") == "被开枪带走"
    assert shot.get("last_words"), "被带走的人按常规留遗言"


def test_hunter_cannot_shoot_when_poisoned():
    """标准规则：被女巫毒杀的猎人不能开枪（`forbidden_if: [poison]`）。"""
    loop, _ = build_board(
        skill_plan={("chr_5", 1): {"skill": "毒药", "target": "chr_6"}},
        vote_plan=votes(1, council="chr_7"),
    )

    records, _ = run_turns(loop, 1)

    rec = records[0]
    assert "poison" in causes(rec), "女巫应当毒中猎人"
    assert "shoot" not in causes(rec), "被毒的猎人不能开枪"
    poisoned = next(item for item in rec.deaths if item.get("cause") == "poison")
    assert poisoned["id"] == "chr_6"
    assert "last_words" not in poisoned, "被毒出局不留遗言"


# ── R3 女巫双药 ───────────────────────────────────────────────


def test_witch_uses_only_one_potion_per_night():
    """标准规则：女巫每晚最多用一瓶药。"""
    loop, llm = build_board(
        skill_plan={("chr_5", 1): {"skill": "解药", "target": "chr_7"}},
        vote_plan=votes(1, council="chr_7"),
    )

    records, events = run_turns(loop, 1)

    potions = [item for item in events_of(events, "skill") if item.get("id") == "chr_5"]
    assert len(potions) == 1, "女巫一夜只能用一瓶药"
    charges = loop.state.characters["chr_5"].skill_charges
    assert charges["解药"] == 0, "用掉的那瓶要扣"
    assert charges["毒药"] == 1, "另一瓶不该被动"


def test_witch_cannot_use_the_same_potion_twice():
    """药瓶用完即没：第二夜不能再用同一瓶。"""
    loop, llm = build_board(
        skill_plan={
            ("chr_5", 1): {"skill": "解药", "target": "chr_7"},
            # 第二夜剧本仍然要求用解药——框架必须驳回
            ("chr_5", 2): {"skill": "解药", "target": "chr_8"},
        },
        vote_plan={**votes(1, council="chr_7"), **votes(2, council="chr_8")},
    )

    records, _ = run_turns(loop, 2)

    assert not [item for item in llm.skill_calls if item[0] == "chr_5" and item[1] == 2], (
        "解药用完后第二夜不该还能发动"
    )
    charges = loop.state.characters["chr_5"].skill_charges
    assert charges["解药"] == 0 and charges["毒药"] == 1
    # 框架侧：可用技能里已经没有解药
    available = loop._available_skills("chr_5", loop.cards["chr_5"], "turn", "night")
    assert "解药" not in [item["id"] for item in available]


def test_exclusive_group_blocks_a_second_potion_in_the_same_night():
    """互斥组：同一晚全组只能用一瓶（`exclusive_group: 药`）。"""
    loop, llm = build_board(
        skill_plan={
            ("chr_5", 1): {"skill": "解药", "target": "chr_7"},
            ("chr_9", 1): {"skill": "解药", "target": "chr_8"},
        },
        vote_plan=votes(1),
        extra=(("chr_9", "女巫", "village"),),
    )

    records, events = run_turns(loop, 1)

    potions = [item for item in llm.skill_calls if item[2] in ("解药", "毒药")]
    assert len(potions) == 1, "互斥组：同一晚只应有一瓶药被使用"
    # 声明侧也要钉住：两瓶药在同一个互斥组
    declared = {item["id"]: item.get("exclusive_group") for item in loop.pack.skills_for("女巫")}
    assert declared["解药"] == "药" and declared["毒药"] == "药"


def test_witch_cannot_heal_herself():
    """标准规则：女巫不可自救（解药的目标不能是自己）。"""
    loop, llm = build_board(
        skill_plan={("chr_5", 1): {"skill": "解药", "target": "chr_5"}},
        vote_plan=votes(1, council="chr_5"),
    )

    antidote = next(
        item for item in loop.pack.skills_for("女巫") if item["id"] == "解药"
    )
    assert "chr_5" not in loop._skill_targets("chr_5", antidote), "女巫不能救自己"

    records, _ = run_turns(loop, 1)

    # 模型想救自己也救不了：技能没发动，她照样出局
    assert not [item for item in llm.skill_calls if item[0] == "chr_5"]
    assert loop.state.characters["chr_5"].position == Position.dead
    assert causes(records[0]) == ["consensus_result"]


def test_heal_saves_the_night_target():
    """解药救下被刀的人：那一夜无人出局，且回执不泄露「谁被救了」。"""
    loop, _ = build_board(
        skill_plan={("chr_5", 1): {"skill": "解药", "target": "chr_7"}},
        vote_plan=votes(1, council="chr_7"),
    )

    records, _ = run_turns(loop, 1)

    rec = records[0]
    assert "consensus_result" not in causes(rec), "被救下的人不该出局"
    assert loop.state.characters["chr_7"].position == Position.scene
    assert "healed" not in rec.vote_result and "saved" not in rec.vote_result, (
        "「谁被救了」是私密信息，不能出现在回执里"
    )


# ── 板子本身（回归护栏）────────────────────────────────────────


def test_standard_board_matches_the_world_pack():
    """回归护栏：测试用的板子必须与 `professions.yaml: initial_pool` 一致。"""
    pack = build_board()[0].pack
    pool = [str(item) for item in pack.initial_pool]

    assert pool == ["狼人", "狼人", "狼人", "预言家", "女巫", "猎人", "村民", "村民"]
    assert set(WOLVES) and set(GODS) and set(VILLAGERS)
