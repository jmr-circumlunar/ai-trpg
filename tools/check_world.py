"""世界包自检：单独检查一个世界包（内容清点 + 引用完整性 + 能否开局）。

用法：
    python tools/check_world.py longzu            # 检查一个
    python tools/check_world.py                   # 检查全部
    python tools/check_world.py longzu --json     # 机器可读

与 `check_contract.py` 的分工：
- `check_contract.py`：代码 ↔ 规则 ↔ 世界（跨包的契约点）
- `check_world.py`：**单个世界包内部**的完整性（写世界包时的第一道关）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from tools.check_contract import check_world  # noqa: E402
from trpg.rulespack.validate import validate_declarations  # noqa: E402


def _yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def inventory(world_dir: Path) -> dict:
    """内容清点（写世界包时对照用）。"""
    world = _yaml(world_dir / "world.yaml")
    timeline = _yaml(world_dir / "timeline.yaml")
    origins = _yaml(world_dir / "origins.yaml")
    paths = _yaml(world_dir / "paths.yaml")
    items = _yaml(world_dir / "items.yaml")
    seeds = _yaml(world_dir / "seeds.yaml")
    npcs = _yaml(world_dir / "npcs.yaml")
    tw = _yaml(world_dir / "npcs_timeline.yaml")

    regions = {}
    region_dir = world_dir / "regions"
    if region_dir.is_dir():
        for md in sorted(region_dir.glob("*.md")):
            text = md.read_text(encoding="utf-8")
            rows = [ln for ln in text.splitlines() if ln.strip().startswith("| loc_")]
            regions[md.stem] = len(rows)

    return {
        "regions": regions,
        "places": sum(regions.values()),
        "npcs": len(npcs.get("npcs") or []),
        "npc_windows": len(tw.get("windows") or {}),
        "origins": len(origins.get("origins") or []),
        "talents": len(origins.get("talent_pool") or []),
        "paths": len(paths.get("paths") or []),
        "items": len(items.get("items") or []),
        "seeds": len(seeds.get("seeds") or []),
        "timelines": [
            {"id": t.get("id"), "available": bool(t.get("available"))}
            for t in (timeline.get("timeline") or [])
        ],
        "core_docs": len(list((world_dir / "core").glob("*.md"))) if (world_dir / "core").is_dir() else 0,
        "archive_docs": len(list((world_dir / "archive").glob("*.md"))) if (world_dir / "archive").is_dir() else 0,
        "has_meta_hooks": (world_dir / "meta_hooks.yaml").exists(),
    }


def check_one(world_id: str, root: Path = _ROOT) -> tuple[list[str], dict]:
    base = root / "worlds" / world_id
    if not base.exists():
        return ([f"世界包不存在：{world_id}"], {})
    errors = check_world(base, root)
    # 装配声明（systems / turn_flow / dice / memory / win_condition）也要查：
    # 这个 import 以前一直没被调用，等于门禁只查了内容清单、没查声明是否成立——
    # 世界包把阶段名或胜负 kind 写错，`check_world` 照样全绿。
    errors += validate_declarations(base)
    return errors, inventory(base)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="世界包自检")
    parser.add_argument("world", nargs="?", default="", help="世界 id（空 = 全部）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--root", default=str(_ROOT))
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    worlds_dir = root / "worlds"
    targets = [args.world] if args.world else sorted(
        d.name for d in worlds_dir.iterdir() if d.is_dir() and not d.name.startswith("_")
    )

    report: dict = {}
    failed = 0
    for wid in targets:
        errors, inv = check_one(wid, root)
        report[wid] = {"errors": errors, "inventory": inv}
        failed += len(errors)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if failed else 0

    for wid, data in report.items():
        inv = data["inventory"]
        print(f"\n=== {wid} ===")
        if inv:
            print(
                f"  地点 {inv['places']} / 区域 {len(inv['regions'])} · NPC {inv['npcs']}"
                f"（窗口 {inv['npc_windows']}）· 种子 {inv['seeds']} · 装备 {inv['items']}"
            )
            print(
                f"  出身 {inv['origins']} · 天赋 {inv['talents']} · 身份 {inv['paths']}"
                f" · core {inv['core_docs']} 篇 · archive {inv['archive_docs']} 篇"
                f" · 局外价目表 {'有' if inv['has_meta_hooks'] else '无'}"
            )
            print(
                "  时间点：" + "、".join(
                    f"{t['id']}{'（已解锁）' if t['available'] else '（未解锁）'}" for t in inv["timelines"]
                )
            )
        for err in data["errors"]:
            print(f"  ✗ {err}")
        if not data["errors"]:
            print("  ✓ 检查通过")

    print(f"\n共 {len(targets)} 个世界包，{failed} 处问题")

    # S6-3：晋升补丁待确认提示（stale 风格：**只提醒，不算错误**）
    pending_dir = root / "data" / "promotions"
    pending = sorted(pending_dir.glob("*.yaml")) if pending_dir.is_dir() else []
    if pending:
        print(f"\n· 待确认的物品晋升补丁 {len(pending)} 份（运行时只生成补丁，不写世界包）：")
        for path in pending[:5]:
            print(f"    - {path.name}")
        if len(pending) > 5:
            print(f"    … 还有 {len(pending) - 5} 份")
        print("  人工确认后合并：python tools/apply_promotions.py --apply")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
