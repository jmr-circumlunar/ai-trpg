"""物品晋升：把被反复使用的叙事道具**提议**进世界包（人工确认后才生效）。

判据（知识库 `02.1-数据契约` §六 / `02.2-规则数值` §十四）：
- 同局使用 ≥ `prop_to_instance` → 可升为 **instance**（这局生效，装备实例）
- 跨局出现 ≥ `instance_to_template` → 可升为 **template**（写进 `items.yaml`，需人工确认）

**架构约束（02.9 禁止清单：数据层只读）**：

| 阶段 | 谁做 | 写哪 |
|---|---|---|
| 运行时（API） | 代码 | 只生成**待确认补丁** `data/promotions/<world>__<canonical>.yaml`（本地产物，不进世界包） |
| 人工确认 | `tools/apply_promotions.py` | 追加写进 `worlds/<id>/items.yaml` |

这样运行时永远不改世界包；写回是**一次显式的人工动作**。
写回时只**追加文本块**、不重排文件——`items.yaml` 里有大量注释，重排会毁掉它们。

doc: 03-模块/M08-装备与物品/00-模块总述.md
module: M08
layer: L3
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

# 待确认补丁目录（相对仓库根；不放在 worlds/ 里，避免污染世界包）
PENDING_DIR = "data/promotions"


def template_id_for(canonical: str) -> str:
    """由规范化名生成稳定模板 id（同名 → 同一个 id，不会重复追加）。"""
    digest = hashlib.sha1(canonical.strip().encode("utf-8")).hexdigest()[:8]
    return f"tpl_{digest}"


def build_patch(
    world_id: str,
    canonical: str,
    slot: str = "trinket",
    tier: int = 1,
    tags: list[str] | None = None,
    req_level: int = 1,
) -> dict:
    """生成一个待确认补丁（还没进世界包）。"""
    canonical = canonical.strip()
    tid = template_id_for(canonical)
    return {
        "world_id": world_id,
        "canonical": canonical,
        "template_id": tid,
        "item": {
            "id": tid,
            "name": canonical,
            "slot": slot,
            "tier": int(tier),
            "req_level": int(req_level),
            "attrs": {},
            "tags": list(tags or []),
        },
    }


def collect_promotions(
    root: Path | str,
    world_id: str,
    rows: list[dict] | None = None,
    *,
    params: dict | None = None,
) -> list[Path]:
    """按「**道具 × Agent** 使用频率」收集晋升候选，**只生成待确认补丁**。

    - `rows` 由调用方给出（存储访问在 M03 层：`item_usage_candidates_by_agent`），
      这里**不反向依赖存储**；
    - `params` 来自 `rules/equipment.yaml: promotion`（`prop_to_instance` 等阈值）；
    - **运行时绝不写 `worlds/`**：产物只落在 `data/promotions/*.yaml`，人工确认才合并；
    - 幂等：同一 (world, canonical) 只有一个补丁文件（重跑覆盖同一份）。
    """
    cfg = dict(params or {})
    min_uses = int(cfg.get("prop_to_instance") or 0)
    if min_uses <= 0:
        return []
    out: list[Path] = []
    for row in rows or []:
        if int(row.get("uses") or 0) < min_uses:
            continue
        canonical = str(row.get("canonical") or "").strip()
        if not canonical:
            continue
        patch = build_patch(world_id, canonical)
        patch["agent_id"] = str(row.get("agent_id") or "")
        patch["uses"] = int(row.get("uses") or 0)
        patch["sessions"] = int(row.get("sessions") or 0)
        out.append(write_pending(root, patch))
    return out


def pending_path(root: Path | str, world_id: str, canonical: str) -> Path:
    """待确认补丁的落盘位置。"""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in canonical.strip())
    return Path(root) / PENDING_DIR / f"{world_id}__{safe}.yaml"


def write_pending(root: Path | str, patch: dict) -> Path:
    """把补丁写进待确认目录（幂等：同 canonical 覆盖同一文件）。"""
    path = pending_path(root, patch["world_id"], patch["canonical"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(patch, f, allow_unicode=True, sort_keys=False)
    return path


def read_pending(root: Path | str) -> list[dict]:
    """读全部待确认补丁。"""
    base = Path(root) / PENDING_DIR
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.glob("*.yaml")):
        try:
            with path.open("r", encoding="utf-8") as f:
                out.append(yaml.safe_load(f) or {})
        except Exception:  # noqa: BLE001
            continue
    return out


def append_item_template(
    world_dir: Path,
    *,
    template_id: str,
    name: str,
    slot: str = "trinket",
    tier: int = 1,
    tags: list[str] | None = None,
    req_level: int = 1,
) -> str:
    """把模板追加到 `items.yaml` 的 items 列表末尾（保留原注释与顺序）。

    **只给人工工具用**（`tools/apply_promotions.py`）；运行时 API 不许调。
    """
    path = Path(world_dir) / "items.yaml"
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path}")
    lines = path.read_text(encoding="utf-8").splitlines()

    # 已经存在同名 id → 不重复追加（幂等）
    if any(ln.strip() == f"- id: {template_id}" for ln in lines):
        return template_id

    last_item = None
    for i, ln in enumerate(lines):
        if ln.startswith("  - id:"):
            last_item = i
    block = [
        f"  - id: {template_id}",
        f"    name: {name}",
        f"    slot: {slot}",
        f"    tier: {int(tier)}",
        f"    req_level: {int(req_level)}",
        "    attrs: {}",
        "    tags: [" + ", ".join(tags or []) + "]",
    ]
    if last_item is None:
        lines += ["", "items:"] + block
    else:
        end = len(lines)
        for i in range(last_item + 1, len(lines)):
            ln = lines[i]
            if ln.strip() and not ln.startswith("    "):
                end = i
                break
        lines[end:end] = block
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return template_id
