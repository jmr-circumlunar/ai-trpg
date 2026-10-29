"""能力包覆盖链：显式优先级 + 合并回写 + 每次保存留版本备份。

形态（联网核对 2026-10-29：append-only 事件存储 / 版本化配置备份）：
- 覆盖链 = 一串能力包，覆盖优先级显式：priority 数字大者胜；相同则链上靠后者覆盖靠前者；
- 合并结果回写到 data/abilities/merged/<name>.yaml；
- 每次保存先把上一版备份到 data/backups/（可回溯）。

世界内容仍在世界包：本模块只处理能力包之间的覆盖关系，
合并产物落在 data/（运行时不写 worlds/）。

doc: 03-模块/M18-规则包与能力包/00-模块总述.md
module: M18
layer: L2
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

MERGED_DIR = "data/abilities/merged"
BACKUP_DIR = "data/backups"


def _merge_by_id(base: list[Any], override: list[Any]) -> list[Any]:
    """列表按 id 合并：同名覆盖，新的追加（保持原有顺序）。

    解锁树就是这种列表 —— 能力包叠加时应该**并集**，不是整段替换。
    """
    out = [dict(x) if isinstance(x, dict) else x for x in base]
    index = {}
    for pos, item in enumerate(out):
        if isinstance(item, dict) and item.get("id"):
            index[str(item["id"])] = pos
    for item in override:
        if isinstance(item, dict) and item.get("id") and str(item["id"]) in index:
            pos = index[str(item["id"])]
            merged = dict(out[pos])
            merged.update(item)
            out[pos] = merged
        else:
            out.append(item)
    return out


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        current = target.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            _deep_merge(current, value)
        elif isinstance(value, list) and isinstance(current, list):
            target[key] = _merge_by_id(current, value)
        else:
            target[key] = value


def merge_chain(packs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """按覆盖链合并能力包，返回合并后的声明。

    - 每个包形如 {id, priority: int, ...}；
    - 优先级显式：priority 大的覆盖小的；相同则链上靠后的覆盖靠前的；
    - 逐键深合并（dict 递归），列表整体替换。
    """
    merged: dict[str, Any] = {}
    ordered: list[tuple[int, int, dict[str, Any]]] = []
    for index, pack in enumerate(packs or []):
        if isinstance(pack, dict):
            ordered.append((int(pack.get("priority") or 0), index, pack))
    for _, _, pack in sorted(ordered, key=lambda item: (item[0], item[1])):
        _deep_merge(merged, pack)
    return merged


def save_merged(root, merged, name="abilities"):
    """把合并结果回写，并备份上一版。返回 (新文件, 备份文件或 None)。"""
    root = Path(root)
    out = root / MERGED_DIR / (name + ".yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if out.exists():
        backup_dir = root / BACKUP_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat().replace(":", "").replace("-", "")
        backup = backup_dir / ("abilities__" + name + "__" + stamp + ".yaml")
        shutil.copy2(out, backup)
    with out.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(merged, handle, allow_unicode=True, sort_keys=False)
    return out, backup


def unlock_nodes(packs):
    """从能力包声明里取出解锁树节点（按合并后的结果更准）。"""
    merged = merge_chain(packs)
    nodes = merged.get("unlocks") or []
    return [n for n in nodes if isinstance(n, dict)]
