from pathlib import Path

import pytest

from trpg.world import WorldPackError, discover_worlds, load_world

ROOT = Path(__file__).resolve().parents[1]


def test_discover_worlds():
    worlds = discover_worlds(ROOT)
    assert "ironvale" in worlds
    assert all(not w.startswith("_") for w in worlds)


def test_load_ironvale():
    pack = load_world("ironvale", ROOT)
    assert pack.world_id == "ironvale"
    assert pack.name == "铁谷"
    assert "medieval" in pack.eras
    assert pack.eras["medieval"].tier_max == 3
    assert "north_port" in pack.regions
    assert len(pack.seeds) >= 6
    assert len(pack.items) >= 8
    assert len(pack.npcs) >= 5
    assert pack.taboos, "世界包必须声明禁忌"


def test_missing_world_raises():
    with pytest.raises(WorldPackError):
        load_world("no-such-world", ROOT)


def test_unknown_era_raises():
    pack = load_world("ironvale", ROOT)
    with pytest.raises(WorldPackError):
        pack.era("cyberpunk")


def test_seeds_filtered_by_stage():
    pack = load_world("ironvale", ROOT)
    stage1 = pack.seeds_for("medieval", 1)
    stage4 = pack.seeds_for("medieval", 4)
    assert stage1 and stage4
    # stage 1 不应出现只在 3~4 幕才有的事件
    assert all(s["stage_range"][0] <= 1 for s in stage1)
    assert all(s["stage_range"][1] >= 4 for s in stage4)


def test_starter_region_from_characters():
    pack = load_world("ironvale", ROOT)
    # 角色出身地优先
    assert pack.starter_region(["old_quarter"]) == "old_quarter"
    # 未知出身地 -> 回落默认
    assert pack.starter_region(["atlantis"]) == pack.default_region
    # 空列表 -> 默认
    assert pack.starter_region([]) == pack.default_region


def test_tier_max_and_tags_exist():
    pack = load_world("ironvale", ROOT)
    era = pack.era("medieval")
    assert era.allowed_tags
    assert "blade" in era.allowed_tags
