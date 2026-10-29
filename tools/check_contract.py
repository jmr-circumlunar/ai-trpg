"""契约校验：代码内三方一致（rules/*.yaml ↔ worlds/* ↔ 模型常量）。

对应知识库 `07-AI项目/AI跑团/02.8-契约点索引.md` 的 C4–C16。
「文档 ↔ 实现」那一半（B 类）不在这里做：文档在 Obsidian vault，跨库读会制造脆弱依赖。

用法：
    python tools/check_contract.py            # 人类可读
    python tools/check_contract.py --quiet     # 只打错误

退出码 0 = 全部通过，1 = 有差异。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# 直接跑本脚本时也要能 import trpg.*（仓库根进 sys.path）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

ROOT = Path(__file__).resolve().parents[1]

QUALITY_KEYS = {"rough", "common", "fine", "rare", "legendary"}
GRANT_KEYS = {"skills", "stats", "tags", "flags"}
REQUIRED_WORLD_FILES = ["world.yaml", "items.yaml", "seeds.yaml", "npcs.yaml"]

_LOC_RE = re.compile(r"^\|\s*(loc_[A-Za-z0-9_]+)\s*\|", re.M)


def _yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _errs(items: list[str]) -> list[str]:
    return [e for e in items if e]


# ── 规则文件 ────────────────────────────────────────────────
def check_rules(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    eq = _yaml(root / "rules" / "equipment.yaml")
    clk = _yaml(root / "rules" / "clock.yaml")
    ruleset = _yaml(root / "rules" / "ruleset.yaml")

    # C7 tag 词表
    vocab = list(eq.get("tag_vocab") or [])
    if len(vocab) != 20:
        errors.append(f"C7 tag_vocab 应为 20 个，实际 {len(vocab)}")
    for era, cfg in (eq.get("era_pool") or {}).items():
        extra = set((cfg or {}).get("allow") or []) - set(vocab)
        if extra:
            errors.append(f"C7 era_pool[{era}] 含词表外 tag：{sorted(extra)}")

    # C4 成色键
    q = eq.get("quality") or {}
    if set(q) != QUALITY_KEYS:
        errors.append(f"C4 quality 键应为 {sorted(QUALITY_KEYS)}，实际 {sorted(q)}")

    # C8 tier 区间
    if set(eq.get("tier_range") or {}) != {1, 2, 3, 4, 5}:
        errors.append("C8 tier_range 必须覆盖 tier 1~5")
    for key in ("stage_quality_mod",):
        if set(eq.get(key) or {}) != {1, 2, 3, 4}:
            errors.append(f"C8 {key} 必须覆盖 stage 1~4")

    # C5 晋升阈值
    promo = eq.get("promotion") or {}
    for key in ("prop_to_instance", "instance_to_template"):
        if not isinstance(promo.get(key), int) or promo.get(key) < 1:
            errors.append(f"C5 promotion.{key} 必须是正整数")
    if not isinstance(promo.get("require_review"), bool):
        errors.append("C5 promotion.require_review 必须是 bool")

    # C6 时间
    lo = clk.get("min_step_minutes", 5)
    hi = clk.get("max_step_minutes", 1440)
    if not (0 < int(lo) < int(hi)):
        errors.append(f"C6 clock 上下限非法：min={lo} max={hi}")
    steps = [int(s.get("minutes") or 0) for s in (clk.get("allowed_steps") or [])]
    if steps != sorted(steps):
        errors.append("C6 allowed_steps 必须递增")
    out = [s for s in steps if not (int(lo) <= s <= int(hi))]
    if out:
        errors.append(f"C6 allowed_steps 越出 [{lo},{hi}]：{out}")

    # C23 状态词表（conditions 是封闭词表，叙事短语必须被拦在外面）
    cond_vocab = list(ruleset.get("condition_vocab") or [])
    if not cond_vocab:
        errors.append("C23 ruleset.condition_vocab 不能为空")
    elif "dying" not in cond_vocab:
        errors.append("C23 condition_vocab 必须含 dying（濒死判定依赖它）")

    # C9 技能表
    skills = list((ruleset.get("skill_attr") or {}))
    if len(skills) != 12:
        errors.append(f"C9 skill_attr 应为 12 个技能，实际 {len(skills)}")
    attrs = set(ruleset.get("attributes") or [])
    bad = {v for v in (ruleset.get("skill_attr") or {}).values()} - attrs
    if bad:
        errors.append(f"C9 skill_attr 引用了未定义属性：{sorted(bad)}")
    return errors


# ── 单个世界包 ──────────────────────────────────────────────
def check_world(base: Path, root: Path = ROOT) -> list[str]:
    wid = base.name
    tag = f"[{wid}]"
    eq = _yaml(root / "rules" / "equipment.yaml")
    vocab = set(eq.get("tag_vocab") or [])
    skills = set((_yaml(root / "rules" / "ruleset.yaml").get("skill_attr") or {}))
    errors: list[str] = []

    # C10 必填文件
    for name in REQUIRED_WORLD_FILES:
        if not (base / name).exists():
            errors.append(f"{tag} C10 缺 {name}")

    meta = _yaml(base / "world.yaml")
    if not meta.get("world_id"):
        errors.append(f"{tag} C10 world.yaml 缺 world_id")

    # C15 region entry 与地点 id
    loc_ids: dict[str, set[str]] = {}
    region_dir = base / "regions"
    if region_dir.is_dir():
        for md in sorted(region_dir.glob("*.md")):
            loc_ids[md.stem] = set(_LOC_RE.findall(md.read_text(encoding="utf-8")))
    for rid, cfg in (meta.get("regions") or {}).items():
        ids = loc_ids.get(rid) or set()
        if not ids:
            errors.append(f"{tag} C15 region {rid} 没有地点表")
        entry = (cfg or {}).get("entry")
        if entry and entry not in ids:
            errors.append(f"{tag} C15 region {rid} 的 entry={entry} 不在地点表里")

    # C11 origins / C9 天赋技能
    org = _yaml(base / "origins.yaml")
    origins = list(org.get("origins") or [])
    origin_ids = {str(o.get("id")) for o in origins}
    for o in origins:
        missing = [k for k in ("label", "description", "personality", "talents", "stats_mod", "cost") if not o.get(k)]
        if missing:
            errors.append(f"{tag} C11 origin {o.get('id')} 缺 {missing}")
        if not (o.get("private_goals") or []):
            errors.append(f"{tag} C11 origin {o.get('id')} 缺 private_goals")
        if int(o.get("weight") or 0) < 0:
            errors.append(f"{tag} C11 origin {o.get('id')} weight 为负")
        for attr, mod in (o.get("stats_mod") or {}).items():
            if not 3 <= 10 + int(mod) <= 18:
                errors.append(f"{tag} C11 origin {o.get('id')} stats_mod {attr}{mod:+d} 越界")
    for t in org.get("talent_pool") or []:
        extra = set((t.get("skills") or {})) - skills
        if extra:
            errors.append(f"{tag} C9 天赋 {t.get('id')} 引用了非法技能：{sorted(extra)}")

    # C12 paths
    pdoc = _yaml(base / "paths.yaml")
    paths = list(pdoc.get("paths") or [])
    path_ids = {str(p.get("id")) for p in paths}
    defaults = [p.get("id") for p in paths if p.get("default")]
    if paths and len(defaults) != 1:
        errors.append(f"{tag} C12 default 身份必须恰好 1 条，实际 {defaults}")
    for p in paths:
        if not p.get("granted_by"):
            errors.append(f"{tag} C12 path {p.get('id')} 缺 granted_by")
        req = p.get("requirements") or {}
        for pre in req.get("requires_path") or []:
            if pre not in path_ids:
                errors.append(f"{tag} C12 path {p.get('id')} requires_path={pre} 不存在")
        grade = req.get("min_grade")
        if grade and origin_ids and grade not in origin_ids:
            errors.append(f"{tag} C12 path {p.get('id')} min_grade={grade} 不是出身 id")
        for key in (p.get("grants") or {}):
            if key not in GRANT_KEYS:
                errors.append(f"{tag} C12 path {p.get('id')} grants 含未知键 {key}")

    # C13 seeds.grants_path
    by_path = {str(p.get("id")): p for p in paths}
    for s in (_yaml(base / "seeds.yaml").get("seeds") or []):
        for g in s.get("grants_path") or []:
            if g not in path_ids:
                errors.append(f"{tag} C13 seed {s.get('id')} grants_path={g} 不存在")
                continue
            need = int(((by_path[g].get("requirements") or {}).get("stage_min")) or 1)
            lo = int((s.get("stage_range") or [1, 4])[0])
            if lo < need:
                errors.append(f"{tag} C13 seed {s.get('id')} stage {lo} 早于 {g} 的 stage_min={need}")

    # C14 starter / items
    items_doc = _yaml(base / "items.yaml")
    tpl = {str(i.get("id")): i for i in (items_doc.get("items") or [])}
    for iid, item in tpl.items():
        extra = set(item.get("tags") or []) - vocab
        if extra:
            errors.append(f"{tag} C7 item {iid} 含词表外 tag：{sorted(extra)}")
        if len(item.get("tags") or []) > 3:
            errors.append(f"{tag} C7 item {iid} tag 超过 3 个")
    starter = items_doc.get("starter") or {}
    picks = list(starter.get("default") or [])
    for oid, extra in (starter.get("by_origin") or {}).items():
        if origin_ids and oid not in origin_ids:
            errors.append(f"{tag} C14 starter.by_origin 的 {oid} 不是出身 id")
        picks.extend(extra or [])
    for tid in picks:
        if tid not in tpl:
            errors.append(f"{tag} C14 starter 引用不存在的模板 {tid}")
        elif int(tpl[tid].get("req_level", 1)) != 1:
            errors.append(f"{tag} C14 starter 的 {tid} req_level 必须为 1（新生等级 1）")

    # C16 NPC 窗口单一来源
    npcs = list((_yaml(base / "npcs.yaml").get("npcs") or []))
    windows = (_yaml(base / "npcs_timeline.yaml").get("windows") or {})
    if npcs and windows:
        nid = {str(n.get("id")) for n in npcs}
        wid = {str(k) for k in windows}
        if nid != wid:
            errors.append(
                f"{tag} C16 npcs 与 windows 不一致：仅档案 {sorted(nid - wid)[:3]} / 仅窗口 {sorted(wid - nid)[:3]}"
            )
    for n in npcs:
        if "availability" in n:
            errors.append(f"{tag} C16 npc {n.get('id')} 仍有 availability（应只在 npcs_timeline.yaml）")

    # C7 era tag 白名单
    for era_file in sorted((base / "eras").glob("*.yaml")) if (base / "eras").is_dir() else []:
        era = _yaml(era_file)
        extra = set(era.get("allowed_tags") or []) - vocab
        if extra:
            errors.append(f"{tag} C7 era {era_file.stem} 含词表外 tag：{sorted(extra)}")
        tier_max = int(era.get("tier_max") or 0)
        if not 1 <= tier_max <= 5:
            errors.append(f"{tag} C8 era {era_file.stem} tier_max 越界：{tier_max}")

    # C31 meta_hooks：价目表里的 target 必须在本世界真实存在
    hooks_path = base / "meta_hooks.yaml"
    if hooks_path.exists():
        try:
            from trpg.growth.hooks import load_hooks

            hooks = load_hooks(base, base.name)
            talent_ids = {str(t.get("id")) for t in org.get("talent_pool") or []}
            for item in hooks.spend_rules():
                kind = str(item.get("kind"))
                target = str(item.get("target"))
                if kind == "skill" and target not in skills:
                    errors.append(f"{tag} C31 价目表引用了不存在的技能：{target}")
                elif kind == "talent" and target not in talent_ids:
                    errors.append(f"{tag} C31 价目表引用了不存在的天赋：{target}")
                elif kind == "path" and target not in path_ids:
                    errors.append(f"{tag} C31 价目表引用了不存在的身份：{target}")
                elif kind == "item" and target not in tpl:
                    errors.append(f"{tag} C31 价目表引用了不存在的装备：{target}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{tag} C31 meta_hooks 非法：{exc}")

    # C32 内容门槛：没有事件种子就开不了局
    seeds_doc = _yaml(base / "seeds.yaml")
    if not (seeds_doc.get("seeds") or []):
        errors.append(f"{tag} C32 世界包没有事件种子，开不了局")

    return errors


def check_worlds(root: Path = ROOT) -> list[str]:
    base = root / "worlds"
    if not base.is_dir():
        return ["[worlds] 目录不存在"]
    errors: list[str] = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and (d / "world.yaml").exists():
            errors.extend(check_world(d, root))
    return errors


def check_all(root: Path = ROOT) -> list[str]:
    return _errs(check_rules(root)) + _errs(check_worlds(root))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="契约校验（代码 ↔ rules ↔ worlds）")
    parser.add_argument("--quiet", action="store_true", help="只输出错误")
    parser.add_argument("--root", default=str(ROOT), help="仓库根目录")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    errors = check_all(root)
    if errors:
        print(f"契约校验失败：{len(errors)} 处")
        for e in errors:
            print("  -", e)
        return 1
    if not args.quiet:
        worlds = [
            d.name
            for d in sorted((root / "worlds").iterdir())
            if d.is_dir() and not d.name.startswith("_") and (d / "world.yaml").exists()
        ]
        print(f"契约校验通过：rules 1 套 / worlds {len(worlds)} 个（{', '.join(worlds)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
