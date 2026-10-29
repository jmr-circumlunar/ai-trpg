"""P8-T1：真实模型冒烟集（默认跳过，需显式开启）。

为什么要有它：354+ 个测试全在 mock 下跑，"叙事是否合理"零覆盖。
接下来要连续改 GM 行为（P5 沉浸规则），没有这条红线，改一次飘一次。

开启方式：
    $env:TRPG_SMOKE=1; .\\.venv\\Scripts\\python.exe -m pytest tests\\test_smoke_scenario.py -q

代价：真实模型约 25~30s/回合 × 8 回合 ≈ 4 分钟，所以不进默认套件。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
TURNS = int(os.environ.get("TRPG_SMOKE_TURNS", "8"))

pytestmark = pytest.mark.skipif(
    not os.environ.get("TRPG_SMOKE"),
    reason="真实模型冒烟集：设置 TRPG_SMOKE=1 才跑（每次约 4 分钟）",
)

# 叙事里不许出现的规则术语（数值可以偶尔出现在对话里，术语不行）
FORBIDDEN_TERMS = ("HP", "DC", "tier", "d20", "判定值", "难度值")
# 只在"地下/实验室"语境才该出现的词，出现在学院主区前两幕就是跑偏
UNDERGROUND_WORDS = ("实验体", "样本", "培养槽", "收容失效")


@pytest.fixture(scope="module")
def run():
    from trpg.game import build_cards, build_state
    from trpg.world import load_world

    from trpg.engine.loop import GameLoop
    from trpg.game import build_llm

    pack = load_world(os.environ.get("TRPG_SMOKE_WORLD", "longzu"), ROOT)
    cards = build_cards(2, pack, seed="smoke")
    state = build_state(pack, cards, seed="smoke")
    llm = build_llm(ROOT, None, pack, cards, "auto", None, "smoke", None)
    loop = GameLoop(None, cards, state, llm, seed="smoke", pack=pack)
    return loop, TURNS


def test_no_rule_terms_leak_into_narration(run):
    """叙事里不得出现 HP / DC / tier 这类规则术语。"""
    import asyncio

    loop, turns = run
    bad: list[str] = []
    for _ in range(turns):
        rec = asyncio.run(loop.step())
        text = rec.scene + "\n" + "\n".join(rec.narration)
        for term in FORBIDDEN_TERMS:
            if term in text:
                bad.append(f"第{rec.turn}回合出现 {term}：{text[:60]}")
    assert not bad, "叙事泄露规则术语：\n" + "\n".join(bad[:5])


def test_every_turn_offers_actions(run):
    """每回合都该有 1~4 条可立刻做的事（P4 的 options）。"""
    import asyncio

    loop, turns = run
    missing = []
    for _ in range(turns):
        rec = asyncio.run(loop.step())
        if not rec.options:
            missing.append(rec.turn)
    assert not missing, f"这些回合没有任何可选项：{missing}"


def test_creature_stays_out_of_campus_early(run):
    """学院主区、前两幕不该出现地下实验相关的东西（P5 前为软警告）。"""
    import asyncio

    loop, turns = run
    hits = []
    for _ in range(turns):
        rec = asyncio.run(loop.step())
        text = rec.scene + "\n" + "\n".join(rec.narration)
        if loop.state.stage <= 2 and loop.state.scene.region == "campus":
            for word in UNDERGROUND_WORDS:
                if word in text:
                    hits.append(f"第{rec.turn}回合（幕次{loop.state.stage}）出现「{word}」")
    if hits:
        print("\n[冒烟警告] 学院早幕出现地下元素（P5-T1 种子空间约束负责修）：")
        for h in hits[:5]:
            print("  -", h)
    # P5 落地后这里应改为 assert not hits
    assert True
