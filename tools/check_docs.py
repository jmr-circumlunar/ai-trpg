"""文档 ↔ 代码 双向一致性检查（M14/约束区共用门禁）。

四件事：
① 代码 → 文档：每个 .py 头部 `doc:` 指向的笔记必须存在（--strict 时缺头即失败）
② 文档 → 代码：知识库能力文档 frontmatter `code:` 里的路径必须存在
③ 依赖合规：实际 import 必须 ⊆ `trpg/modules.yaml` 声明的 depends_on（+ 同层白名单）
④ 覆盖率：报告 doc 头覆盖、code 字段覆盖

用法：
    .venv\\Scripts\\python.exe tools\\check_docs.py
    .venv\\Scripts\\python.exe tools\\check_docs.py --strict          # 任一缺失即失败
    .venv\\Scripts\\python.exe tools\\check_docs.py --vault "E:/.../AI员工知识库"

退出码：0 通过 / 1 有违规。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TRPG = ROOT / "trpg"
MODULES_YAML = TRPG / "modules.yaml"

DOC_KEYS = ("doc", "module", "layer")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def dump(self) -> None:
        for msg in self.warnings:
            print(f"[warn] {msg}")
        for msg in self.errors:
            print(f"[FAIL] {msg}")
        print(f"\n错误 {len(self.errors)} 条 / 警告 {len(self.warnings)} 条")


def load_config(report: Report) -> dict:
    data = yaml.safe_load(MODULES_YAML.read_text(encoding="utf-8")) or {}
    if not data.get("modules"):
        report.error("trpg/modules.yaml 缺少 modules 段")
    return data


def package_index(cfg: dict, report: Report) -> dict[str, dict]:
    """包路径 → {layer, module_id, doc}"""
    index: dict[str, dict] = {}
    for mod in cfg.get("modules", []):
        for pkg in mod.get("packages") or []:
            index[pkg] = {
                "layer": int(mod["layer"].lstrip("L")),
                "module": mod["id"],
                "doc": mod.get("doc", ""),
                "status": mod.get("status", "生效"),
            }
            if not (ROOT / pkg).exists():
                report.error(f"modules.yaml 声明的包不存在：{pkg}（{mod['id']}）")
    for plat in cfg.get("platform") or []:
        index[plat["package"]] = {
            "layer": int(str(plat["layer"]).lstrip("L")),
            "module": "",
            "doc": "",
            "status": "平台",
        }
    return index


def check_packages_declared(index: dict[str, dict], report: Report) -> None:
    """目录里每个 trpg/<pkg> 都要被声明（否则它就是个没人管的包）。"""
    for path in sorted(TRPG.iterdir()):
        if not path.is_dir() or path.name.startswith("__"):
            continue
        key = f"trpg/{path.name}"
        if key not in index:
            report.error(f"包未在 modules.yaml 登记：{key}")


def doc_header(tree: ast.Module) -> dict[str, str]:
    text = ast.get_docstring(tree) or ""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        for key in DOC_KEYS:
            if stripped.startswith(f"{key}:"):
                out[key] = stripped.split(":", 1)[1].strip()
    return out


def resolve_target(node: ast.ImportFrom, pkg: list[str]) -> list[str]:
    base = pkg[: max(0, len(pkg) - (node.level - 1))] if node.level else []
    parts = list(base)
    if node.module:
        parts += node.module.split(".")
    return parts


def iter_py() -> list[Path]:
    return [p for p in TRPG.rglob("*.py") if "__pycache__" not in p.parts]


def main() -> int:
    parser = argparse.ArgumentParser(description="文档↔代码一致性检查")
    parser.add_argument("--strict", action="store_true", help="doc 头缺失也算失败")
    parser.add_argument("--vault", default="", help="Obsidian vault 根目录（用于校验 code: 字段）")
    args = parser.parse_args()

    report = Report()
    cfg = load_config(report)
    index = package_index(cfg, report)
    check_packages_declared(index, report)

    whitelist = {(str(a), str(b)) for a, b in (cfg.get("whitelist") or [])}
    assembly = {item["path"]: int(str(item["layer"]).lstrip("L")) for item in cfg.get("assembly") or []}

    covered_doc = 0
    total = 0
    for path in iter_py():
        rel = path.relative_to(ROOT).as_posix()
        total += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        parts = list(path.relative_to(ROOT).with_suffix("").parts)
        is_init = parts[-1] == "__init__"
        pkg = parts[:-1] if is_init else parts[:-1]
        pkg_key = f"trpg/{pkg[1]}" if len(pkg) > 1 else "trpg"
        own = index.get(pkg_key, {})
        own_layer = assembly.get(rel, own.get("layer", 99))
        own_module = own.get("module", "")

        # ① doc 头
        header = doc_header(tree)
        if header.get("doc"):
            covered_doc += 1
            target_doc = header["doc"]
            if target_doc.startswith("03-模块/") and not args.vault:
                pass  # 未给 vault 时只做格式检查
            elif args.vault and not (Path(args.vault) / target_doc).exists():
                report.error(f"{rel}: doc 头指向的笔记不存在 → {target_doc}")
            if own_module and header.get("module") and header["module"] != own_module:
                report.error(f"{rel}: doc 头 module={header['module']} 与 modules.yaml 的 {own_module} 不一致")
        elif rel not in assembly and not is_init:
            (report.error if args.strict else report.warn)(f"{rel}: 缺少 doc 头")

        # ③ 依赖合规
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = resolve_target(node, pkg)
            if len(target) < 2 or target[0] != "trpg":
                continue
            target_key = f"trpg/{target[1]}"
            if target_key == pkg_key:
                continue
            dest = index.get(target_key)
            if target_key == "trpg/game":
                if own_layer < 6:
                    report.error(f"{rel}:{node.lineno} 只有外壳层（L6+）可引用装配层 game.py")
                continue
            if dest is None:
                report.error(f"{rel}:{node.lineno} import 了未登记的包 {target_key}")
                continue
            if rel in assembly:
                continue
            if not dict(own).get("module") and own.get("status") == "平台":
                if dest["layer"] < own_layer:
                    report.error(f"{rel}:{node.lineno} 平台包反向依赖 {target_key}")
                continue
            # 白名单优先：显式登记的同层/跨层例外，两个方向都认
            if dest["module"]:
                pair_ok = (own_module, dest["module"]) in whitelist or (
                    dest["module"],
                    own_module,
                ) in whitelist
                if pair_ok:
                    continue
            if dest["layer"] > own_layer:
                report.error(
                    f"{rel}:{node.lineno} 向上依赖 {target_key}（L{own_layer} → L{dest['layer']}）"
                )
                continue
            if dest["module"] and dest["module"] not in (own.get("depends") or []):
                mod_deps = next(
                    (m.get("depends_on") or [] for m in cfg["modules"] if m["id"] == own_module), []
                )
                if dest["module"] not in mod_deps:
                    report.error(
                        f"{rel}:{node.lineno} 依赖未声明：{own_module} → {dest['module']}"
                        f"（去 trpg/modules.yaml 加 depends_on）"
                    )

    # ② 文档 → 代码
    docs_total = 0
    if args.vault:
        module_root = Path(args.vault) / cfg.get("vault_root", "") / "03-模块"
        if not module_root.exists():
            report.warn(f"未找到知识库模块目录：{module_root}")
        else:
            for note in module_root.rglob("*.md"):
                text = note.read_text(encoding="utf-8", errors="replace")
                if not text.startswith("---"):
                    continue
                fm = text.split("---", 2)[1]
                meta = yaml.safe_load(fm) or {}
                codes = meta.get("code") or []
                if not codes:
                    continue
                docs_total += 1
                for item in codes:
                    if not (ROOT / str(item)).exists():
                        report.error(f"{note.name}: code 字段悬空 → {item}")

    # ④ 覆盖率
    print(f"代码文件 {total} 个，其中 {covered_doc} 个带 doc 头")
    if args.vault:
        print(f"能力文档 {docs_total} 篇带 code 字段")
    report.dump()
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
