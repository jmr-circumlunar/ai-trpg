"""NPC 与时间线：谁在哪个时间点可遇。

龙族世界包是本地私有的，缺失时整组跳过。
"""

from pathlib import Path

import pytest

from trpg.world import load_world

ROOT = Path(__file__).resolve().parents[1]
LONGZU = ROOT / "worlds" / "longzu"

pytestmark = pytest.mark.skipif(
    not (LONGZU / "world.yaml").exists(), reason="longzu 世界包仅本地私有"
)


@pytest.fixture(scope="module")
def pack():
    return load_world("longzu", ROOT)


def test_default_timeline_is_first_available(pack):
    assert pack.default_timeline == "academy_year1"


def test_grade_gap_matches_canon(pack):
    """学院按年份分级：恺撒 2007（高两级）、楚子航/诺诺 2008（高一级）、玩家 2009。"""
    y1 = {n["id"] for n in pack.npcs_for("academy_year1")}
    y2 = {n["id"] for n in pack.npcs_for("academy_year2")}
    y3 = {n["id"] for n in pack.npcs_for("academy_year3")}

    # 恺撒高两级：第一年在，第二年毕业
    assert "npc_caesar" in y1
    assert "npc_caesar" not in y2

    # 楚子航、诺诺高一级：第二年仍在，第三年毕业
    assert "npc_chu" in y2
    assert "npc_chu" not in y3
    assert "npc_nono" in y2

    # 芬格尔留级，一直在
    assert "npc_fingel" in y3

    # 伊莎贝尔低一级：第一年不在，第二年在
    assert "npc_isabel" not in y1
    assert "npc_isabel" in y2


def test_peers_enter_in_year1(pack):
    y1 = {n["id"] for n in pack.npcs_for("academy_year1")}
    assert "npc_lu_mingfei" in y1, "路明非与玩家同期入学"
    assert "npc_zero" in y1
    assert "npc_newbie_poor" in y1


def test_later_npcs_not_in_year1(pack):
    y1 = {n["id"] for n in pack.npcs_for("academy_year1")}
    y2 = {n["id"] for n in pack.npcs_for("academy_year2")}

    assert "npc_lu_mingze" not in y1
    assert "npc_lu_mingze" in y2
    assert "npc_suenxi" not in y1


def test_japan_line_never_in_academy_years(pack):
    for tid in ("academy_year1", "academy_year2", "academy_year3"):
        ids = {n["id"] for n in pack.npcs_for(tid)}
        for japan in ("npc_genchiyo", "npc_erii", "npc_tachibana", "npc_sakurai"):
            assert japan not in ids, f"{japan} 不应出现在 {tid}"


def test_staff_always_available(pack):
    for tid in ("academy_year1", "academy_year2", "academy_year3"):
        ids = {n["id"] for n in pack.npcs_for(tid)}
        assert "npc_angier" in ids
        assert "npc_schneider" in ids


def test_timeline_order_loaded(pack):
    assert pack._timeline_order["academy_year1"] == 1
    assert pack._timeline_order["academy_year3"] == 3
    assert len(pack.timeline) >= 4
