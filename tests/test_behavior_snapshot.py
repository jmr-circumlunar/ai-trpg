"""行为快照：`ironvale` 缺省配置必须逐字节不变。

P0 起每批改动（白名单裁剪、拆 step、插 pipeline）都要拿它证明「行为零变化」。
快照只取稳定字段，避免把随机 id、耗时类字段卷进来。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.builders import make_card  # noqa: E402
from trpg.agents import MockLLM  # noqa: E402
from trpg.engine.loop import GameLoop  # noqa: E402
from trpg.models import (  # noqa: E402
    CharacterRuntime,
    GameState,
    Party,
    Scene,
    WorldRef,
)
from trpg.rules.loader import Rules  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "ironvale_turn3.json"
TURNS = 3
SEED = 42
# 只快照「骨架」：谁行动、做了什么、判定结果、被拒项、版本号
SNAPSHOT_FIELDS = (
    "turn",
    "actors",
    "actions",
    "outcomes",
    "rejected",
    "deaths",
    "loot",
    "path_changes",
    "version",
)


def build_snapshot() -> list[dict]:
    """跑 3 回合，返回可 JSON 化的骨架。gen_golden.py 复用同一函数。"""
    cards = {}
    for i in range(3):
        cid = f"chr_p{i + 1}"
        cards[cid] = make_card(cid=cid, profession=["盗贼", "战士", "学者"][i])

    state = GameState(
        session_id="ses_golden01",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(location_id="loc_a", region="north_port"),
        party=Party(gold=10, supplies=3),
        characters={
            cid: CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)
            for cid, card in cards.items()
        },
    )
    loop = GameLoop(Rules(ROOT), cards, state, MockLLM(SEED), seed=SEED)
    records = asyncio.run(loop.run(TURNS))

    rows = []
    for rec in records:
        rows.append({name: getattr(rec, name, None) for name in SNAPSHOT_FIELDS})
    # 兜一层 default=str：TurnRecord 里可能有非 JSON 原生类型（如枚举/路径）
    return json.loads(json.dumps(rows, ensure_ascii=False, default=str))


def test_golden_baseline_exists():
    assert GOLDEN.exists(), (
        "golden 基线缺失。请先确认代码处于基线状态，再运行：\n"
        "  .\\.venv\\Scripts\\python.exe tools\\gen_golden.py"
    )


def test_ironvale_turn3_matches_golden():
    if not GOLDEN.exists():
        pytest.fail("golden 基线缺失，先运行 tools/gen_golden.py 生成")

    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    actual = build_snapshot()

    assert actual == expected, (
        "ironvale 缺省行为发生了变化。若这是有意的行为变更，必须同步更新 golden 并在决策日志登记；"
        "否则说明改动破坏了「缺省即现状」。"
    )
