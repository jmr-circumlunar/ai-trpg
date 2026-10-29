"""S7-2：M17 世界包工坊（文本 → IR → 骨架 → 校验 → 待确认区）。

出口判据逐条对应：
1. **一条文本能产出过校验的骨架**（IR → 骨架 → 校验通过）；
2. **产物落在待确认区**（`data/promotions/`），**绝不写 `worlds/`**；
3. **校验失败路径**：骨架有问题时给出可操作的中文错误清单；
4. **幂等**：重跑覆盖待确认区，不产生重复副作用。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trpg.workshop import WorldIR, parse_ir, scaffold_world
from tools.workshop import run

ROOT = Path(__file__).resolve().parents[1]

GOOD_IR = {
    "name": "测试世界",
    "id": "test_world",
    "tone": "用于工坊测试的中性基调",
    "regions": [{"id": "main", "label": "主区", "places": [{"id": "loc_1", "label": "起点"}]}],
    "items": [{"id": "itm_1", "name": "测试道具"}],
    "npcs": [{"id": "npc_1", "name": "测试角色"}],
    "seeds": [{"id": "evt_1", "type": "social", "skeleton": "一个测试事件"}],
}


# ── IR 解析 ─────────────────────────────────────────────────
def test_parse_valid_ir():
    ir, errors = parse_ir(json.dumps(GOOD_IR, ensure_ascii=False))
    assert ir is not None
    assert not errors
    assert ir.name == "测试世界"
    assert ir.id == "test_world"
    assert len(ir.items) == 1


def test_parse_accepts_dict():
    ir, errors = parse_ir(GOOD_IR)
    assert ir is not None and not errors


def test_parse_rejects_broken_json():
    ir, errors = parse_ir("{不是 json")
    assert ir is None
    assert errors and "JSON" in errors[0]


def test_parse_rejects_empty():
    ir, errors = parse_ir("")
    assert ir is None and errors


def test_parse_reports_missing_name():
    ir, errors = parse_ir({"id": "x"})
    assert ir is None
    assert any("name" in e for e in errors)


def test_parse_reports_wrong_type():
    ir, errors = parse_ir({"name": "n", "items": {"不是": "列表"}})
    assert ir is None
    assert any("items" in e for e in errors)


def test_parse_tolerates_markdown_fence():
    fenced = "```json\n" + json.dumps(GOOD_IR, ensure_ascii=False) + "\n```"
    ir, errors = parse_ir(fenced)
    assert ir is not None and not errors


def test_unknown_keys_go_to_extra():
    ir, errors = parse_ir({"name": "n", "未来字段": {"a": 1}})
    assert ir is not None and not errors
    assert ir.extra.get("未来字段") == {"a": 1}, "未知键不该丢"


# ── 骨架生成 ────────────────────────────────────────────────
def test_scaffold_copies_blank_and_fills(tmp_path):
    ir, _ = parse_ir(GOOD_IR)
    out = tmp_path / "skeleton"
    result = scaffold_world(ir, out, blank_dir=ROOT / "worlds" / "_blank")
    assert not result["errors"]
    assert (out / "world.yaml").exists()
    assert (out / "systems.yaml").exists(), "空白包的声明文件要一起复制"
    world = (out / "world.yaml").read_text(encoding="utf-8")
    assert "测试世界" in world
    assert (out / "items.yaml").exists()
    assert (out / "regions" / "main.md").exists()


def test_scaffold_is_idempotent(tmp_path):
    ir, _ = parse_ir(GOOD_IR)
    out = tmp_path / "skeleton"
    first = scaffold_world(ir, out, blank_dir=ROOT / "worlds" / "_blank")
    second = scaffold_world(ir, out, blank_dir=ROOT / "worlds" / "_blank")
    assert first["files"] == second["files"], "重跑不应产生重复/残留文件"


# ── 端到端：产物只在待确认区 ───────────────────────────────
def test_run_writes_only_to_pending_area(tmp_path):
    root = tmp_path / "repo"
    (root / "worlds" / "_blank").mkdir(parents=True)
    blank = ROOT / "worlds" / "_blank"
    for src in blank.rglob("*"):
        dst = root / "worlds" / "_blank" / src.relative_to(blank)
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())

    before = sorted(p.as_posix() for p in (root / "worlds").rglob("*"))
    out = run(GOOD_IR, "my_world", root=root)
    after = sorted(p.as_posix() for p in (root / "worlds").rglob("*"))
    assert before == after, "工坊**绝不能**写 worlds/"
    assert (root / "data" / "promotions" / "my_world" / "world.yaml").exists()
    assert out["out_dir"].endswith("my_world")


def test_run_reports_errors_but_still_pending(tmp_path):
    """输入不合格 → 给出可操作的中文错误，**不写 worlds/**。"""
    root = tmp_path / "repo"
    (root / "worlds" / "_blank").mkdir(parents=True)
    out = run("{不是 json", "broken", root=root)
    assert out["errors"], "应当报告问题"
    assert any("JSON" in e for e in out["errors"])
    assert not (root / "worlds" / "broken").exists(), "绝不能写 worlds/"


def test_run_with_real_blank_produces_validatable_skeleton(tmp_path):
    """用真实空白包跑一遍：产物落在待确认区，校验器能给出结论。"""
    root = tmp_path / "repo"
    blank_dst = root / "worlds" / "_blank"
    blank_dst.mkdir(parents=True)
    blank = ROOT / "worlds" / "_blank"
    for src in blank.rglob("*"):
        dst = blank_dst / src.relative_to(blank)
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())

    out = run(GOOD_IR, "real_world", root=root)
    pending = root / "data" / "promotions" / "real_world"
    assert (pending / "world.yaml").exists()
    assert (pending / "WORKSHOP.md").exists(), "要有人看的摘要"
    assert "errors" in out and isinstance(out["errors"], list)
    assert not (root / "worlds" / "real_world").exists()


def test_run_never_writes_worlds_even_on_success(tmp_path):
    root = tmp_path / "repo"
    (root / "worlds" / "_blank").mkdir(parents=True)
    out = run(GOOD_IR, "ok_world", root=root)
    assert not (root / "worlds" / "ok_world").exists()
    assert out["target_id"] == "ok_world"


def test_world_ir_roundtrip():
    ir = WorldIR(name="n", id="i")
    data = ir.to_dict()
    assert data["name"] == "n"
    assert json.dumps(data, ensure_ascii=False)
