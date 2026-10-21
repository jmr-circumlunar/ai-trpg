"""世界包加载：worlds/<world_id>/ 是一个数据目录，加世界不改代码。

目录约定（见 worlds/_template）：
    world.yaml          基调、禁忌、组织、默认 era/region
    eras/<era>.yaml     技术水平、可用 tag、tier 上限
    regions/<region>.md 地点、势力、NPC
    seeds.yaml          事件种子
    items.yaml          装备模板
    npcs.yaml           NPC 模板
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class WorldPackError(ValueError):
    pass


@dataclass
class EraConfig:
    id: str
    label: str
    tech_level: str = "medieval"
    tier_max: int = 3
    allowed_tags: list[str] = field(default_factory=list)


@dataclass
class WorldPack:
    world_id: str
    name: str
    tone: str = ""
    style: str = "冷峻、克制，少形容词"
    taboos: list[str] = field(default_factory=list)
    default_era: str = ""
    default_region: str = ""
    organizations: dict[str, Any] = field(default_factory=dict)
    eras: dict[str, EraConfig] = field(default_factory=dict)
    regions: list[str] = field(default_factory=list)
    seeds: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    npcs: list[dict] = field(default_factory=list)
    root: Path | None = None

    # ── 查询 ────────────────────────────────────────────────
    def era(self, era_id: str) -> EraConfig:
        era = self.eras.get(era_id)
        if era is None:
            raise WorldPackError(
                f"世界 {self.world_id} 没有 era '{era_id}'（可用：{', '.join(self.eras)}）"
            )
        return era

    def seeds_for(self, era_id: str, stage: int, seed_type: str | None = None) -> list[dict]:
        out = []
        for seed in self.seeds:
            eras = seed.get("era") or []
            if eras and era_id not in eras:
                continue
            lo, hi = (seed.get("stage_range") or [1, 4])[:2]
            if not (lo <= stage <= hi):
                continue
            if seed_type and seed.get("type") != seed_type:
                continue
            out.append(seed)
        return out

    def starter_region(self, origin_regions: list[str]) -> str:
        """开局点 = 角色出身地的交集（有内容者优先），否则用默认 region。"""
        known = [r for r in origin_regions if r in self.regions]
        if known:
            return known[0]
        if self.default_region:
            return self.default_region
        if self.regions:
            return self.regions[0]
        raise WorldPackError(f"世界 {self.world_id} 没有任何 region")


def _read_yaml(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or default


def load_world(world_id: str, root: Path | str) -> WorldPack:
    base = Path(root) / "worlds" / world_id
    world_file = base / "world.yaml"
    if not world_file.exists():
        raise WorldPackError(f"找不到世界包：{world_file}")

    meta = _read_yaml(world_file, {}) or {}
    if not meta.get("world_id"):
        raise WorldPackError(f"{world_file} 缺少 world_id")

    # eras
    eras: dict[str, EraConfig] = {}
    era_dir = base / "eras"
    if era_dir.is_dir():
        for f in sorted(era_dir.glob("*.yaml")):
            data = _read_yaml(f, {}) or {}
            eid = data.get("id") or f.stem
            eras[eid] = EraConfig(
                id=eid,
                label=data.get("label", eid),
                tech_level=data.get("tech_level", "medieval"),
                tier_max=int(data.get("tier_max", 3)),
                allowed_tags=list(data.get("allowed_tags") or []),
            )
    if not eras:
        # 允许在 world.yaml 里内联一个 era
        for eid, data in (meta.get("eras") or {}).items():
            eras[eid] = EraConfig(
                id=eid,
                label=(data or {}).get("label", eid),
                tech_level=(data or {}).get("tech_level", "medieval"),
                tier_max=int((data or {}).get("tier_max", 3)),
            )
    if not eras:
        raise WorldPackError(f"世界 {world_id} 没有定义任何 era")

    # regions：目录里的 .md + world.yaml 里声明的
    regions = list((meta.get("regions") or {}).keys())
    region_dir = base / "regions"
    if region_dir.is_dir():
        for f in sorted(region_dir.glob("*.md")):
            if f.stem not in regions:
                regions.append(f.stem)
    if not regions:
        regions = [meta.get("default_region") or "default"]

    seeds = (_read_yaml(base / "seeds.yaml", {}) or {}).get("seeds") or []
    items = (_read_yaml(base / "items.yaml", {}) or {}).get("items") or []
    npcs = (_read_yaml(base / "npcs.yaml", {}) or {}).get("npcs") or []

    default_era = meta.get("default_era") or next(iter(eras))
    if default_era not in eras:
        raise WorldPackError(f"default_era '{default_era}' 不在 era 列表里")

    return WorldPack(
        world_id=world_id,
        name=meta.get("name", world_id),
        tone=str(meta.get("tone") or "").strip(),
        style=meta.get("style") or "冷峻、克制，少形容词",
        taboos=list(meta.get("taboos") or []),
        default_era=default_era,
        default_region=meta.get("default_region") or regions[0],
        organizations=meta.get("organizations") or {},
        eras=eras,
        regions=regions,
        seeds=seeds,
        items=items,
        npcs=npcs,
        root=base,
    )


def discover_worlds(root: Path | str) -> list[str]:
    base = Path(root) / "worlds"
    if not base.is_dir():
        return []
    return sorted(
        d.name
        for d in base.iterdir()
        if d.is_dir() and not d.name.startswith("_") and (d / "world.yaml").exists()
    )
