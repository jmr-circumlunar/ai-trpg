"""世界包工坊：文本 → 中间表示 → YAML 骨架 → 校验 → **待确认补丁**（S7-2）。

用法：
    python tools/workshop.py --ir-file ir.json --target-id my_world   # 离线入口（IR 已备好）
    python tools/workshop.py --ir-file ir.json --target-id my_world --check-only

**红线**：本工具**绝不写 `worlds/`**。产物只落 `data/promotions/<target_id>/`，
由人确认后用现成的 `tools/apply_promotions.py --apply` 范式合并。

三段形态（D-7）：抽取（IR）→ 生成（骨架）→ 校验修补（不通过也给清单）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trpg.rulespack.validate import validate_declarations  # noqa: E402
from trpg.workshop import WorldIR, parse_ir, scaffold_world  # noqa: E402

_PENDING = _ROOT / "data" / "promotions"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _diff_note(ir: WorldIR, out_dir: Path, root: Path) -> str:
    """人看的摘要：这次生成了什么、待确认区在哪。"""
    counts = {
        "eras": len(ir.eras),
        "regions": len(ir.regions),
        "professions": len(ir.professions),
        "items": len(ir.items),
        "npcs": len(ir.npcs),
        "seeds": len(ir.seeds),
    }
    body = "\n".join(f"  {k}: {v}" for k, v in counts.items() if v)
    try:
        shown = out_dir.relative_to(root).as_posix()
    except ValueError:
        shown = out_dir.as_posix()
    return (
        f"# 世界包工坊产物（待确认）\n\n"
        f"目标 id：{ir.id or ir.name}\n\n"
        f"骨架目录：`{shown}`\n\n"
        f"内容条目：\n{body or '  （空）'}\n\n"
        f"确认后再合并（**本工具不写 worlds/**）：\n"
        f"  python tools/apply_promotions.py --apply --world {ir.id or ir.name}\n"
    )


def run(
    ir_payload: str | dict,
    target_id: str,
    *,
    root: Path = _ROOT,
    check_only: bool = False,
) -> dict:
    """跑一次工坊：IR → 骨架 → 校验 → 待确认区。

    返回 `{"ok", "errors", "files", "out_dir", "target_id"}`：
    - `ok`：骨架是否通过校验；
    - `errors`：解析 / 校验的全部问题（可操作的中文说明）；
    - `out_dir`：产物目录（**待确认区**）。
    """
    errors: list[str] = []
    ir, parse_errors = parse_ir(ir_payload)
    if ir is None:
        return {
            "ok": False,
            "errors": list(parse_errors),
            "files": [],
            "out_dir": "",
            "target_id": target_id,
        }
    errors.extend(parse_errors)

    out_dir = Path(root) / "data" / "promotions" / str(target_id)
    blank = Path(root) / "worlds" / "_blank"
    result = scaffold_world(ir, out_dir, blank_dir=blank)
    errors.extend(result.get("errors") or [])

    # 校验闸：声明层用现成的校验器（tools/check_world 用的是同一套）
    try:
        errors.extend(validate_declarations(out_dir))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"校验器异常：{exc}")

    # 内容清单：骨架必须至少有 world.yaml
    if not (out_dir / "world.yaml").exists():
        errors.append("骨架缺 world.yaml")

    (out_dir / "WORKSHOP.md").write_text(_diff_note(ir, out_dir, Path(root)), encoding="utf-8")

    return {
        "ok": not errors,
        "errors": errors,
        "files": list(result.get("files") or []),
        "out_dir": out_dir.as_posix(),
        "target_id": target_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="世界包工坊：文本 → 骨架 → 待确认补丁")
    parser.add_argument("--ir-file", required=True, help="中间表示 JSON 文件")
    parser.add_argument("--target-id", required=True, help="目标世界 id（待确认区目录名）")
    parser.add_argument("--check-only", action="store_true", help="只校验，不落待确认区")
    parser.add_argument("--root", default=str(_ROOT))
    args = parser.parse_args(argv)

    payload = _read_json(Path(args.ir_file))
    if args.check_only:
        ir, errors = parse_ir(payload)
        if ir is None:
            for err in errors:
                print(f"[FAIL] {err}")
            return 1
        print(f"[OK] IR 解析通过：{ir.name}")
        return 0

    out = run(payload, args.target_id, root=Path(args.root))
    for err in out["errors"]:
        print(f"[FAIL] {err}")
    if out["ok"]:
        print(f"[OK] 骨架已生成（待确认）：{out['out_dir']}")
        print(f"     共 {len(out['files'])} 个文件；人工确认后再合并到 worlds/")
        return 0
    print(f"[WARN] 骨架已生成但有问题（仍只落在待确认区）：{out['out_dir']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
