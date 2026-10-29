"""世界包加载：worlds/<world_id>/ 是一个数据目录，加世界不改代码。

目录约定（见 worlds/_blank）：
    world.yaml          基调、禁忌、组织、默认 era/region
    eras/<era>.yaml     技术水平、可用 tag、tier 上限
    regions/<region>.md 地点、势力、NPC
    seeds.yaml          事件种子
    items.yaml          装备模板
    npcs.yaml           NPC 模板
    professions.yaml    单层职业（旧世界用：ironvale）
    origins.yaml        出身（血统）+ 天赋池（三层职业世界用：longzu）
    paths.yaml          身份阶梯（剧情授予，可被 StateDelta 改）

doc: 03-模块/M05-世界包/00-模块总述.md
module: M05
layer: L2
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
    # 三层职业：出身（不可变）/ 天赋池（开局抽）/ 身份阶梯（剧情可变）
    origins: list[dict] = field(default_factory=list)
    talent_pool: list[dict] = field(default_factory=list)
    talent_count: tuple[int, int] = (1, 2)
    origins_random: bool = True
    paths: list[dict] = field(default_factory=list)
    paths_default: str = ""
    # 报到初始装备（模板 id）
    starter_default: list[str] = field(default_factory=list)
    starter_by_origin: dict[str, list[str]] = field(default_factory=dict)
    # regions 的原始配置（含 entry 落地地点）
    region_meta: dict[str, dict] = field(default_factory=dict)
    # 日历：`calendar.start_date` 决定「第 1 天」是哪一天（空 = 该世界无日历）
    calendar: dict[str, Any] = field(default_factory=dict)
    # 地点表解析缓存（region -> locations）
    _loc_cache: dict[str, list[dict]] = field(default_factory=dict, repr=False)
    # 分层：core 常驻 prompt；archive 进向量库按需检索
    core_text: str = ""
    archive_docs: list[dict] = field(default_factory=list)
    # 时间线：时间点列表 + NPC 时间窗口
    timeline: list[dict] = field(default_factory=list)
    default_timeline: str = ""
    _timeline_order: dict[str, int] = field(default_factory=dict, repr=False)
    _windows: dict[str, dict] = field(default_factory=dict, repr=False)
    # 隐藏信息可见性矩阵（`world.yaml: secrets`）：空 = 不共享任何隐藏信息
    secrets: dict[str, Any] = field(default_factory=dict)
    # 观察者视角配置（`world.yaml: visibility`）：`thoughts` = god / participant / all
    visibility: dict[str, Any] = field(default_factory=dict)
    # 胜负条件（`world.yaml: win_condition`）：空 = 沿用「全员出局」的旧语义
    win_condition: dict[str, Any] = field(default_factory=dict)
    # 人数（`world.yaml: players`）：default/min/max。空 = 沿用旧行为（前端给人数选择）
    players: dict[str, Any] = field(default_factory=dict)
    # 阵营显示名（`world.yaml: camp_labels`）：前端是通用层，不该硬编码阵营含义
    camp_labels: dict[str, Any] = field(default_factory=dict)
    # 界面声明（`world.yaml: ui`）：layout 选注册布局，theme 指向本包 ui/theme.css
    ui: dict[str, Any] = field(default_factory=dict)
    # 主动技能（`skills.yaml`）：空 = 这个世界没有技能系统（存量世界因此零改动）
    skills: list[dict] = field(default_factory=list)
    # Lorebook 设定条目（`lorebook.yaml`，可选）：词条触发用；空 = 该世界没有词条触发
    lorebook_entries: list[dict] = field(default_factory=list)
    # 死亡原因显示文案（`world.yaml: death_labels`）：原因码 → 显示文案
    death_labels: dict[str, Any] = field(default_factory=dict)
    # 知识视图声明（`knowledge.yaml`，可选）：空 = 该世界不启用
    knowledge: dict[str, Any] = field(default_factory=dict)
    root: Path | None = None
    # 装配结果缓存：由 rulespack.assemble 写入。world 层只提供「存数据的槽位」，
    # 不做装配逻辑——否则会变成 world → rulespack 的向上依赖（见 02-约束/01 §一）。
    _caps_cache: Any = field(default=None, repr=False, compare=False)

    # ── 装配声明（只读数据；装配逻辑在 M18） ──────────────────

    def _read_optional_yaml(self, name: str) -> dict[str, Any]:
        """读世界包根目录下的可选声明文件；不存在返回空 dict。

        实现委托给模块级 `_read_yaml`——这里原来自己写了一份 `yaml.safe_load`，
        两个入口的「缺文件怎么办」「类型错了怎么办」迟早会分叉。
        """
        if self.root is None:
            return {}
        return dict(_read_yaml(self.root / name, {}, require_mapping=True))

    def systems_declaration(self) -> dict[str, Any]:
        """能力开关声明。

        - 文件不存在 / 无 `systems:` 键 → 返回空 dict（= 已实现系统全部开启）
        - 有 `systems:` 键 → 返回该映射
        - 也允许把系统直接写在根键下（无 `systems:` 包裹）
        """
        data = self._read_optional_yaml("systems.yaml")
        raw = data.get("systems", data)
        if not isinstance(raw, dict):
            raise WorldPackError("systems.yaml: systems 必须是 mapping")
        return dict(raw)

    def turn_flow_declaration(self) -> dict[str, Any]:
        """回合流程声明；缺省返回空 dict（= 用框架 standard preset）。"""
        return self._read_optional_yaml("turn_flow.yaml")

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

    # ── 势力（B2：能力查询，判定侧只拿数字）───────────────
    def faction_label(self, faction_id: str) -> str:
        cfg = self.organizations.get(str(faction_id or "")) or {}
        return str(cfg.get("label") or faction_id or "")

    def faction_attitude(self, faction_id: str) -> int:
        """势力对玩家的基础态度（`world.yaml: organizations`）。

        只读：一局里不会变。可变关系走 M03 的关系表（B37），不在这里。
        """
        cfg = self.organizations.get(str(faction_id or "")) or {}
        try:
            return int(cfg.get("attitude_to_player") or 0)
        except (TypeError, ValueError):
            return 0

    def faction_of_npc(self, npc_id: str) -> str:
        key = str(npc_id or "")
        if not key:
            return ""
        for npc in self.npcs:
            if str(npc.get("id")) == key:
                return str(npc.get("org") or "")
        return ""

    def social_faction(self, npc_id: str, region: str) -> str:
        """一次社交判定面对的是哪个势力：目标 NPC → 本区 authority → 空。"""
        faction = self.faction_of_npc(npc_id)
        if faction:
            return faction
        meta = (self.region_meta or {}).get(str(region or "")) or {}
        for nid in meta.get("authority") or []:
            faction = self.faction_of_npc(str(nid))
            if faction:
                return faction
        return ""

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

    # ── 主动技能（`skills.yaml`，可选） ─────────────────────────

    def skills_for(self, profession: str, trigger: str = "") -> list[dict]:
        """某身份在某个触发时机可用的技能声明。

        `trigger` 为空 = 不限时机。**这里只做筛选，不做效果判断**：
        怎么结算由 `engine/loop.py` 的 `skill` 执行体决定。
        """
        target_prof = str(profession or "")
        out: list[dict] = []
        for item in self.skills:
            if str(item.get("profession") or "") != target_prof:
                continue
            if trigger and str(item.get("trigger") or "turn") != trigger:
                continue
            out.append(item)
        return out

    def skill_by_id(self, skill_id: str) -> dict | None:
        for item in self.skills:
            if str(item.get("id")) == str(skill_id):
                return item
        return None

    # ── 三层职业（origin / talent / path）───────────────────────
    @property
    def has_origins(self) -> bool:
        """世界包是否启用三层职业体系（有 origins.yaml 且至少一条出身）。"""
        return bool(self.origins)

    def origins_pool(self) -> list[dict]:
        """可被抽到的出身：权重 > 0 才算进池（权重 0 = 保留数据但不开）。"""
        return [o for o in self.origins if int(o.get("weight") or 0) > 0]

    def origin(self, oid: str) -> dict | None:
        for item in self.origins:
            if str(item.get("id")) == str(oid):
                return item
        return None

    def origin_label(self, oid: str) -> str:
        cfg = self.origin(oid)
        return str((cfg or {}).get("label") or oid) if oid else ""

    def path(self, pid: str) -> dict | None:
        for item in self.paths:
            if str(item.get("id")) == str(pid):
                return item
        return None

    def path_ids(self) -> list[str]:
        return [str(p.get("id")) for p in self.paths if p.get("id")]

    def path_label(self, pid: str) -> str:
        cfg = self.path(pid)
        return str((cfg or {}).get("label") or pid) if pid else ""

    def path_default(self) -> str:
        """开局身份：显式 default 优先，其次标了 `default: true` 的，最后取第一条。"""
        if self.paths_default and self.path(self.paths_default):
            return self.paths_default
        for item in self.paths:
            if item.get("default"):
                return str(item.get("id"))
        return self.path_ids()[0] if self.paths else ""

    def starter_items(self, origin_id: str = "") -> list[str]:
        """报到时的初始装备模板：default 人人有，by_origin 按出身追加。"""
        out = list(self.starter_default)
        for tid in self.starter_by_origin.get(str(origin_id or ""), []):
            if tid not in out:
                out.append(tid)
        return out

    def item_template(self, tid: str) -> dict | None:
        for item in self.items:
            if str(item.get("id")) == str(tid):
                return item
        return None

    def region_entry(self, region: str) -> str:
        """该区域的落地地点 id；没配 entry 就退化为 loc_<region>。"""
        cfg = self.region_meta.get(region) or {}
        return str(cfg.get("entry") or f"loc_{region}")

    @property
    def calendar_start(self) -> str:
        """「第 1 天」对应的真实日期（`world.yaml: calendar.start_date`）。空 = 无日历。"""
        return str((self.calendar or {}).get("start_date") or "")

    def locations(self, region: str) -> list[dict]:
        """解析 `regions/<id>.md` 的地点表：第一列 `loc_` id、第二列中文名、第三列钩子。

        这些表格本来就是作者写好的内容资产（longzu 26 个地点），
        解析它比另建一份 `locations.yaml` 少一份真相源。
        """
        cached = self._loc_cache.get(region)
        if cached is not None:
            return cached
        out: list[dict] = []
        if self.root:
            file = self.root / "regions" / f"{region}.md"
            if file.exists():
                for line in file.read_text(encoding="utf-8").splitlines():
                    text = line.strip()
                    if not text.startswith("|"):
                        continue
                    cells = [c.strip() for c in text.strip("|").split("|")]
                    if len(cells) < 2 or not cells[0].startswith("loc_"):
                        continue
                    # 第 4 列「出口」可选：作者给了就用（P2.5 地图连通）
                    exits: list[str] = []
                    if len(cells) > 3:
                        raw = cells[3].replace("，", ",").replace("、", ",").replace("/", ",")
                        exits = [x.strip() for x in raw.split(",") if x.strip().startswith("loc_")]
                    out.append(
                        {
                            "id": cells[0],
                            "label": cells[1],
                            "hook": cells[2] if len(cells) > 2 else "",
                            "exits": exits,
                        }
                    )
        self._loc_cache[region] = out
        return out

    def location_label(self, location_id: str, region: str = "") -> str:
        """地点 id → 中文名。查不到就原样返回（不编造名字）。"""
        order = [region] if region else list(self.regions)
        for rid in order:
            for loc in self.locations(rid):
                if loc["id"] == location_id:
                    return str(loc["label"] or location_id)
        return str(location_id)

    def region_label(self, region: str) -> str:
        """区域 id → 中文名（`world.yaml: regions.<id>.label`）。"""
        return str((self.region_meta.get(region) or {}).get("label") or region)

    def neighbors(self, region: str) -> list[str]:
        """区域邻接（`world.yaml: regions.<id>.adjacent`）。完全固定，不由模型决定。"""
        cfg = self.region_meta.get(region) or {}
        return [str(x) for x in (cfg.get("adjacent") or []) if str(x) in self.regions]

    def timeline_point(self, timeline_id: str = "") -> dict:
        """当前时间点配置（含 start_clock / premise / fixed_events）。"""
        want = timeline_id or self.default_timeline
        for point in self.timeline:
            if str(point.get("id")) == str(want):
                return point
        return {}

    def timeline_available(self, timeline_id: str) -> bool:
        """二期：只有标了 `available` 的时间点才能开局。"""
        point = self.timeline_point(timeline_id)
        return bool(point) and bool(point.get("available"))

    def available_timelines(self) -> list[dict]:
        return [
            {"id": str(p.get("id")), "label": str(p.get("label") or p.get("id")), "era": str(p.get("era") or self.default_era)}
            for p in self.timeline
            if p.get("available")
        ]

    def timeline_era(self, timeline_id: str = "") -> str:
        point = self.timeline_point(timeline_id)
        return str(point.get("era") or self.default_era)

    def start_clock(self, timeline_id: str = "") -> dict:
        """开局时间；缺省退回 Clock 默认（第 1 天 20:00）。"""
        raw = (self.timeline_point(timeline_id) or {}).get("start_clock") or {}
        out: dict[str, int] = {}
        for key in ("day", "hour", "minute"):
            if raw.get(key) is not None:
                try:
                    out[key] = int(raw[key])
                except (TypeError, ValueError):
                    pass
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


def _talent_count(raw: Any) -> tuple[int, int]:
    """`talent_count` 三种写法都收：`[1, 2]` / `{min: 1, max: 2}` / `2`。"""
    lo, hi = 1, 2
    if isinstance(raw, dict):
        lo = int(raw.get("min", lo))
        hi = int(raw.get("max", hi))
    elif isinstance(raw, (list, tuple)) and raw:
        lo = int(raw[0])
        hi = int(raw[1]) if len(raw) > 1 else lo
    elif isinstance(raw, int):
        lo = hi = raw
    lo = max(0, lo)
    hi = max(lo, hi)
    return lo, hi


def _read_yaml(path: Path, default: Any = None, require_mapping: bool = False) -> Any:
    """读世界包里的 YAML（**唯一入口**，`_read_optional_yaml` 也走它）。

    `require_mapping=True` 用于「声明文件」：写成列表要在**这里**报错，
    而不是等到下游 `data.get(...)` 才炸——那时错误信息已经离现场很远了。
    普通数据文件（items / npcs）可能是列表，所以默认不校验。
    """
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return default
    if require_mapping and not isinstance(data, dict):
        raise WorldPackError(f"{path.name} 必须是 mapping，收到 {type(data).__name__}")
    return data


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
    items_doc = _read_yaml(base / "items.yaml", {}) or {}
    items = items_doc.get("items") or []
    starter_doc = items_doc.get("starter") or {}
    starter_default = [str(x) for x in (starter_doc.get("default") or [])]
    starter_by_origin = {
        str(k): [str(x) for x in (v or [])]
        for k, v in (starter_doc.get("by_origin") or {}).items()
    }
    npcs = (_read_yaml(base / "npcs.yaml", {}) or {}).get("npcs") or []
    prof_doc = _read_yaml(base / "professions.yaml", {}) or {}
    professions = prof_doc.get("professions") or []
    initial_pool = [str(x) for x in (prof_doc.get("initial_pool") or [])]

    # 主动技能（可选）：只读数据，怎么生效由框架的 `skill` 执行体决定
    skill_doc = _read_yaml(base / "skills.yaml", {}) or {}
    knowledge = dict(_read_yaml(base / "knowledge.yaml", {}) or {})
    skills = [item for item in (skill_doc.get("skills") or []) if isinstance(item, dict)]
    for item in skills:
        if not item.get("id"):
            raise WorldPackError(f"{world_id}/skills.yaml 有一条技能缺少 id")

    # 三层职业：出身池 + 天赋池（origins.yaml）
    org_doc = _read_yaml(base / "origins.yaml", {}) or {}
    origins = list(org_doc.get("origins") or [])
    talent_pool = list(org_doc.get("talent_pool") or [])
    origins_random = bool(org_doc.get("random", True))
    talent_count = _talent_count(org_doc.get("talent_count"))
    for item in origins:
        if not item.get("id"):
            raise WorldPackError(f"{world_id}/origins.yaml 有一条出身缺少 id")

    # 身份阶梯（paths.yaml）
    path_doc = _read_yaml(base / "paths.yaml", {}) or {}
    paths = list(path_doc.get("paths") or [])
    paths_default = str(path_doc.get("default") or "")
    for item in paths:
        if not item.get("id"):
            raise WorldPackError(f"{world_id}/paths.yaml 有一条身份缺少 id")

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

    # lorebook：设定条目（词条触发）；**可选文件**，不存在 = 该世界没有词条触发
    lore_doc = _read_yaml(base / "lorebook.yaml", {}) or {}
    lorebook_entries = list(lore_doc.get("entries") or [])

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
        origins=origins,
        talent_pool=talent_pool,
        talent_count=talent_count,
        origins_random=origins_random,
        paths=paths,
        paths_default=paths_default,
        starter_default=starter_default,
        starter_by_origin=starter_by_origin,
        region_meta=dict(meta.get("regions") or {}),
        calendar=dict(meta.get("calendar") or {}),
        secrets=dict(meta.get("secrets") or {}),
        # 观察者视角配置（`world.yaml: visibility`）：决定「谁能看到什么」。
        #   thoughts: god | participant | all —— 思维过程（thought）的可见性
        # 缺省 `all`（= 谁都看得到），存量世界因此完全不变。
        visibility=dict(meta.get("visibility") or {}),
        win_condition=dict(meta.get("win_condition") or {}),
        players=dict(meta.get("players") or {}),
        camp_labels=dict(meta.get("camp_labels") or {}),
        ui=dict(meta.get("ui") or {}),
        skills=skills,
        lorebook_entries=lorebook_entries,
        knowledge=knowledge,
        death_labels=dict(meta.get("death_labels") or {}),
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
