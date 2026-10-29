"""M17 · 骨架生成：IR → 世界包文件（S7-2）。

**脚手架来自 `worlds/_blank`**（空白包 + preset）—— 不是从零造一个包，
所以生成的骨架天然满足框架的最小结构要求。

**边界**：只写**目标目录**（工具层给的待确认区），**绝不写 `worlds/`**。

doc: 03-模块/M17-世界包工坊/00-模块总述.md
module: M17
layer: L1
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from .ir import WorldIR

# 骨架里允许被 IR 填充的文件（其余从 _blank 原样复制）
_FILLABLE = (
    "world.yaml",
    "items.yaml",
    "npcs.yaml",
    "seeds.yaml",
    "professions.yaml",
)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def scaffold_world(
    ir: WorldIR,
    target_dir: Path | str,
    *,
    blank_dir: Path | str | None = None,
) -> dict[str, Any]:
    """用 IR 生成世界包骨架到 `target_dir`。

    返回 `{"files": [...], "errors": [...]}`：
    - `files`：实际写出的文件（相对 target_dir）；
    - `errors`：骨架自身的问题（例如 IR 里没有可用内容）——**不抛栈**。
    """
    target = Path(target_dir)
    blank = Path(blank_dir) if blank_dir else Path("worlds") / "_blank"
    errors: list[str] = []

    if not blank.exists():
        return {"files": [], "errors": [f"脚手架不存在：{blank}"]}

    target.mkdir(parents=True, exist_ok=True)
    # 1) 复制空白包（幂等：先清掉上一轮产物，避免残留旧文件）
    if target.exists():
        for child in target.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    for src in sorted(blank.rglob("*")):
        rel = src.relative_to(blank)
        dst = target / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # 2) 填 world.yaml
    world = _read_yaml(target / "world.yaml")
    if ir.name:
        world["name"] = ir.name
    if ir.id:
        world["id"] = ir.id
    if ir.tone:
        world["tone"] = ir.tone
    if ir.players:
        world["players"] = dict(ir.players)
    if ir.win_condition:
        world["win_condition"] = dict(ir.win_condition)
    if ir.ui:
        world["ui"] = dict(ir.ui)
    _write_yaml(target / "world.yaml", world)

    # 3) 填内容表（IR 有才填，缺了保持 _blank 的空表）
    if ir.professions:
        data = _read_yaml(target / "professions.yaml")
        data["professions"] = list(ir.professions)
        _write_yaml(target / "professions.yaml", data)
    if ir.items:
        data = _read_yaml(target / "items.yaml")
        data["items"] = list(ir.items)
        _write_yaml(target / "items.yaml", data)
    if ir.npcs:
        data = _read_yaml(target / "npcs.yaml")
        data["npcs"] = list(ir.npcs)
        _write_yaml(target / "npcs.yaml", data)
    if ir.seeds:
        data = _read_yaml(target / "seeds.yaml")
        data["seeds"] = list(ir.seeds)
        _write_yaml(target / "seeds.yaml", data)
    if ir.eras:
        eras_dir = target / "eras"
        eras_dir.mkdir(parents=True, exist_ok=True)
        for era in ir.eras:
            era_id = str(era.get("id") or "")
            if not era_id:
                errors.append("有一条 era 缺 id，已跳过")
                continue
            _write_yaml(eras_dir / f"{era_id}.yaml", dict(era))

    # 4) 区域：每区一个 md（骨架格式：地点表）
    if ir.regions:
        regions_dir = target / "regions"
        regions_dir.mkdir(parents=True, exist_ok=True)
        for region in ir.regions:
            region_id = str(region.get("id") or "")
            if not region_id:
                errors.append("有一条 region 缺 id，已跳过")
                continue
            lines = [
                f"# {region.get('label') or region_id}",
                "",
                "| id | 名称 | 说明 |",
                "|---|---|---|",
            ]
            for place in region.get("places") or []:
                if not isinstance(place, dict):
                    continue
                lines.append(
                    f"| {place.get('id') or ''} | {place.get('label') or ''} | {place.get('hint') or ''} |"
                )
            (regions_dir / f"{region_id}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not ir.name:
        errors.append("IR 没有名字：生成的骨架只能算空白包")

    files = sorted(
        p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()
    )
    return {"files": files, "errors": errors}
