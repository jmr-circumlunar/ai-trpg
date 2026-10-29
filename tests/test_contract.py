"""契约点测试：把「代码里写死的集合」和「文档列举的契约」钉住。

对应知识库 `07-AI项目/AI跑团/02.8-契约点索引.md`：
- C1/C2/C3：模型字段集与 Delta 白名单（文档锚点写在各断言上方）
- C4/C5/C9：枚举与阈值（与 rules/*.yaml 对比）
- C4–C16 全量：委托 tools/check_contract.check_all()，覆盖 rules 与所有世界包

改字段/白名单/阈值时，这些测试会先红——这是设计意图，不是障碍。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from check_contract import check_all  # noqa: E402

from trpg.engine import state_merge  # noqa: E402
from trpg.models import AIExtended, CharacterRuntime  # noqa: E402
from trpg.rules.loader import Rules  # noqa: E402

# 02.1 §二（AIExtended）—— 增删字段必须同时改文档
CARD_FIELDS = {
    "profession",
    "origin",
    "path",
    "talents",
    "carried",
    "affiliation",
    "origin_region",
    "level",
    "xp",
    "stats",
    "hp",
    "hp_max",
    "sanity",
    "sanity_max",
    "skills",
    "private_goal",
    "fear",
    "bottom_line",
    "equipment",
    "scars",
    "echo",
    "wear",
    "model",
}

# 02.1 §三（CharacterRuntime）
RUNTIME_FIELDS = {
    "hp",
    "sanity",
    "conditions",
    "inventory",
    "position",
    "path",
    "flags",
    "camp",
    "camp_revealed",
    "dying_since",
    # P3：角色级钱与补给（party 降级为团队公共仓）
    "gold",
    "supplies",
    # A2：技能资源与私有笔记（都不进 delta 白名单）
    "skill_charges",
    "notes",
    # S2-1：信念状态（自我信念 + 怀疑度；缺省 None = 没填）
    "mindset",
}


def test_card_fields_match_doc():
    """C1：AIExtended 字段集 = 数据契约 §二。"""
    assert set(AIExtended.model_fields) == CARD_FIELDS


def test_runtime_fields_match_doc():
    """C2：CharacterRuntime 字段集 = 数据契约 §三。"""
    assert set(CharacterRuntime.model_fields) == RUNTIME_FIELDS


def test_delta_does_not_open_flags():
    """C3：白名单不得放开 characters.*.flags（只能由 grants 写；party.flags 不受此限）。"""
    char_patterns = [p.pattern for p in state_merge._ALLOWED_PATTERNS if "characters" in p.pattern]
    assert char_patterns, "白名单缺 characters.* 相关路径"
    assert all("flags" not in p for p in char_patterns), "白名单不应放开 characters.*.flags"


def test_delta_allows_path():
    """C3：白名单必须含 characters.*.path（仅 set，须过身份闸门）。"""
    joined = " ".join(p.pattern for p in state_merge._ALLOWED_PATTERNS)
    assert "path" in joined, "白名单缺 characters.<id>.path"


def test_promotion_thresholds_present():
    """C5：晋升阈值必须在 rules/equipment.yaml 里（不在代码里）。"""
    eq = Rules(ROOT)
    promo = eq.equipment.get("promotion") or {}
    assert promo.get("prop_to_instance", 0) >= 1
    assert promo.get("instance_to_template", 0) >= 1
    assert promo.get("require_review") is True


def test_skill_table_closed():
    """C9：技能表 12 个且封闭（天赋、世界包技能都必须 ⊆）。"""
    rules = Rules(ROOT)
    assert len(rules.skill_attr) == 12


@pytest.mark.parametrize("rule", ["rules", "worlds"])
def test_contract_check_passes(rule: str):
    """C4–C16：rules 与所有世界包的引用完整性。"""
    errors = check_all(ROOT)
    assert errors == [], "\n".join(errors)
