"""把「待确认的物品晋升」真正写进世界包（人工动作）。

为什么要有这一步：[[07-AI项目/AI跑团/02.9-代码架构约束|02.9 架构约束]] 规定**数据层只读**，
运行时不允许改 `worlds/`。所以 API 只生成待确认补丁（`data/promotions/*.yaml`），
由人 review 后执行本工具才生效。

用法：
    python tools/apply_promotions.py            # 列出待确认补丁（不写）
    python tools/apply_promotions.py --apply    # 确认写入世界包
    python tools/apply_promotions.py --apply --world longzu   # 只处理某个世界

退出码 0 = 成功（没有待处理也算成功）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trpg.equipment.promote import (  # noqa: E402
    append_item_template,
    pending_path,
    read_pending,
)


def _backup_items(root: Path, world_id: str) -> Path | None:
    """合并前备份世界包的 items.yaml（人工动作，必须留退路）。

    备份落在 `data/backups/`（**不进世界包**，避免污染世界目录）。
    """
    src = root / "worlds" / world_id / "items.yaml"
    if not src.exists():
        return None
    backup_dir = root / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = backup_dir / f"{world_id}__items__{stamp}.yaml"
    shutil.copy2(src, dst)
    return dst


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="应用待确认的物品晋升")
    parser.add_argument("--apply", action="store_true", help="真正写入世界包（默认只预览）")
    parser.add_argument("--world", default="", help="只处理某个世界")
    parser.add_argument("--root", default=str(_ROOT))
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    patches = read_pending(root)
    if args.world:
        patches = [p for p in patches if str(p.get("world_id")) == args.world]
    if not patches:
        print("没有待确认的晋升补丁。")
        return 0

    print(f"待确认 {len(patches)} 条：\n")
    for patch in patches:
        item = patch.get("item") or {}
        print(f"  - {patch.get('world_id')} / {patch.get('canonical')}")
        print(f"    id={item.get('id')} slot={item.get('slot')} tier={item.get('tier')} tags={item.get('tags')}")

    if not args.apply:
        print("\n（预览模式。确认无误后加 --apply 写入世界包）")
        return 0

    print()
    # 合并前备份（S6-3）：写世界包是人工动作，必须留退路
    for world_id in sorted({str(p.get("world_id")) for p in patches}):
        backup = _backup_items(root, world_id)
        if backup is not None:
            print(f"  · 已备份 {world_id}/items.yaml → {backup}")
    for patch in patches:
        item = patch.get("item") or {}
        world_dir = root / "worlds" / str(patch.get("world_id"))
        try:
            append_item_template(
                world_dir,
                template_id=str(item.get("id")),
                name=str(item.get("name") or patch.get("canonical")),
                slot=str(item.get("slot") or "trinket"),
                tier=int(item.get("tier") or 1),
                tags=list(item.get("tags") or []),
                req_level=int(item.get("req_level") or 1),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {patch.get('canonical')}：{exc}")
            continue
        # 已应用 → 从待确认目录移除
        p = pending_path(root, str(patch.get("world_id")), str(patch.get("canonical")))
        if p.exists():
            p.unlink()
        print(f"  ✓ 已写入 {world_dir / 'items.yaml'}：{item.get('id')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
