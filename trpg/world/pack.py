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
    professions: list[dict] = field(default_factory=list)
    initial_pool: list[str] = field(default_factory=list)
    # 分层：core 常驻 prompt；archive 进向量库按需检索
    core_text: str = ""
    archive_docs: list[dict] = field(default_factory=list)
    # 时间线：时间点列表 + NPC 时间窗口
    timeline: list[dict] = field(default_factory=list)
    default_timeline: str = ""
    _timeline_order: dict[str, int] = field(default_factory=dict, repr=False)
    _windows: dict[str, dict] = field(default_factory=dict, repr=False)
    root: Path | None = None

    def npcs_for(self, timeline_id: str) -> list[dict]:
        """只返回当前时间点可遇的 NPC。

        `enters` 之前不可遇，`leaves` 之后不可遇（含边界）。
        """
        if not timeline_id or not self._timeline_order:
            return list(self.npcs)

        order = self._timeline_order
        current = order.get(timeline_id)
        if current is None:
            return list(self.npcs)

        out = []
        for npc in self.npcs:
            window = self._windows.get(str(npc.get("id"))) or {}
            enters = str(window.get("enters") or "before")
            leaves = window.get("leaves")

            if enters != "before":
                start = order.get(enters)
                if start is None or start > current:
                    continue
            if leaves:
                end = order.get(str(leaves))
                if end is not None and current >= end:
                    continue
            out.append(npc)
        return out

    def organizations_text(self) -> str:
        """势力名单（一句话 + 态度），常驻 prompt —— 细节才去 archive 检索。"""
        if not self.organizations:
            return ""
        lines = []
        for oid, cfg in self.organizations.items():
            cfg = cfg or {}
            label = cfg.get("label", oid)
            attitude = cfg.get("attitude_to_player", 0)
            resources = "、".join(cfg.get("resources") or [])
            tail = f"（可提供：{resources}）" if resources else ""
            lines.append(f"- {label}：对玩家态度 {attitude:+d}{tail}")
        return "\n".join(lines)

    def region_text(self, region: str) -> str:
        """当前区域的详情（进入才注入）。"""
        if not self.root:
            return ""
        file = self.root / "regions" / f"{region}.md"
        if not file.exists():
            return ""
        return file.read_text(encoding="utf-8").strip()

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

    def profession(self, pid: str) -> dict | None:
        for item in self.professions:
            if str(item.get("id")) == pid:
                return item
        return None

    def profession_pool(self) -> list[dict]:
        """一期：按 initial_pool 顺序返回可选职业（没有则全部）。"""
        if not self.initial_pool:
            return list(self.professions)
        picked = [self.profession(p) for p in self.initial_pool]
        return [p for p in picked if p]

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
    prof_doc = _read_yaml(base / "professions.yaml", {}) or {}
    professions = prof_doc.get("professions") or []
    initial_pool = [str(x) for x in (prof_doc.get("initial_pool") or [])]

    # 时间线：时间点与该时间点的 NPC 窗口
    tl_doc = _read_yaml(base / "timeline.yaml", {}) or {}
    timeline = list(tl_doc.get("timeline") or [])
    timeline_order = {
        str(t.get("id")): int(t.get("order", i + 1))
        for i, t in enumerate(timeline)
        if t.get("id")
    }
    default_timeline = next(
        (str(t["id"]) for t in timeline if t.get("available")), 
        str(timeline[0]["id"]) if timeline else "",
    )

    tw_doc = _read_yaml(base / "npcs_timeline.yaml", {}) or {}
    tw_defaults = tw_doc.get("defaults") or {}
    windows: dict[str, dict] = {}
    for nid, cfg in (tw_doc.get("windows") or {}).items():
        cfg = dict(cfg or {})
        kind = cfg.pop("kind", None)
        merged = dict(tw_defaults.get(kind) or {}) if kind else {}
        merged.update(cfg)
        windows[str(nid)] = merged

    # core：常驻设定（几条 md 顺序拼接，控制总量）
    core_parts: list[str] = []
    core_dir = base / "core"
    if core_dir.is_dir():
        for f in sorted(core_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8").strip()
            if text:
                core_parts.append(text)
    core_text = "\n\n".join(core_parts)

    # archive：长文本，进向量库按需检索
    archive_docs: list[dict] = []
    archive_dir = base / "archive"
    if archive_dir.is_dir():
        for f in sorted(archive_dir.rglob("*.md")):
            text = f.read_text(encoding="utf-8").strip()
            if text:
                archive_docs.append(
                    {
                        "path": str(f.relative_to(base)).replace("\\", "/"),
                        "title": f.stem,
                        "text": text,
                    }
                )

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
        professions=professions,
        initial_pool=initial_pool,
        core_text=core_text,
        archive_docs=archive_docs,
        timeline=timeline,
        default_timeline=default_timeline,
        _timeline_order=timeline_order,
        _windows=windows,
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
