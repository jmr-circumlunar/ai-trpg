"""P1-T3 / T4 / T5：地点中文名、区域详情注入、真实日期。

三件事的共同目标是「说人话」：
- 时间从「第 3 天 20:00」变成「1348年4月12日 星期日 黄昏 20:00」
- 地点从 `loc_gate` 变成「校门」（但 id 仍保留，GM 写 delta 要用）
- GM 能看到当前区域的地点表与势力（`region_text` 以前是死代码）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trpg.game import build_cards, build_state
from trpg.agents import prompts
from trpg.models import Clock
from trpg.clock.core import date_label
from trpg.world import WorldPack

ROOT = Path(__file__).resolve().parents[1]


def _pack(world_id: str) -> WorldPack:
    from trpg.world import load_world

    return load_world(world_id, ROOT)


# ── T5 真实日期 ─────────────────────────────────────────────
def test_date_label_uses_start_date_and_minutes():
    clock = Clock(day=1, hour=8, minute=12, start_date="2026-09-01")
    text = date_label(clock)
    assert "2026年9月1日" in text
    assert "星期二" in text
    assert "08:12" in text


def test_date_label_advances_with_day():
    clock = Clock(day=3, hour=23, minute=45, start_date="2026-09-01")
    text = date_label(clock)
    assert "2026年9月3日" in text, "day 是累计值，日期要跟着走"
    assert "23:45" in text, "分钟不能再丢（旧前端固定显示 :00）"


def test_date_label_falls_back_without_calendar():
    assert date_label(Clock(day=3, hour=20, minute=0)) .startswith("第 3 天")


def test_date_label_survives_bad_start_date():
    text = date_label(Clock(day=2, hour=9, start_date="not-a-date"))
    assert text.startswith("第 2 天"), "坏数据不能让页面崩"


def test_packs_declare_calendar():
    assert _pack("longzu").calendar_start == "2026-09-01"
    assert _pack("ironvale").calendar_start == "1348-04-12"


def test_build_state_carries_start_date():
    pack = _pack("longzu")
    cards = build_cards(2, pack, seed="1")
    state = build_state(pack, cards)
    assert state.clock.start_date == "2026-09-01"


# ── T3 地点中文名 ───────────────────────────────────────────
def test_region_locations_are_parsed():
    pack = _pack("longzu")
    locs = pack.locations("campus")
    assert locs, "regions/campus.md 里的地点表必须被解析出来"
    assert all(loc["id"].startswith("loc_") for loc in locs)
    assert all(loc["label"] for loc in locs), "每个地点都要有中文名"


def test_location_label_resolves_to_chinese():
    pack = _pack("longzu")
    loc_id = pack.locations("campus")[0]["id"]
    label = pack.location_label(loc_id, "campus")
    assert label != loc_id, "查得到就必须给中文名"
    assert not label.startswith("loc_")


def test_location_label_unknown_id_is_identity():
    assert _pack("longzu").location_label("loc_not_exist") == "loc_not_exist"


def test_region_label_is_chinese():
    assert _pack("longzu").region_label("campus") == "学院主区"


# ── T4 区域详情进 prompt ────────────────────────────────────
def test_compact_state_shows_label_and_id():
    pack = _pack("longzu")
    cards = build_cards(2, pack, seed="1")
    state = build_state(pack, cards)
    text = prompts.compact_state(state, cards, pack=pack)
    assert "地点：" in text
    assert "loc_" in text, "id 要保留：GM 写 scene.location_id 得用合法 id"
    assert pack.location_label(state.scene.location_id, state.scene.region) in text
    assert "学院主区" in text


def test_scene_prompt_injects_region_detail():
    pack = _pack("longzu")
    cards = build_cards(2, pack, seed="1")
    state = build_state(pack, cards)
    detail = pack.region_text(state.scene.region)
    assert detail, "campus.md 应该有内容"

    user = prompts.gm_scene_user(state, cards, 1, npcs=[], pack=pack)
    assert "当前区域" in user, "区域详情块必须进 prompt（region_text 以前是死代码）"
    # 抽区域文档里的第一个地点名，确认真的注入了内容而不是占位符
    first = pack.locations(state.scene.region)[0]["label"]
    assert first in user
