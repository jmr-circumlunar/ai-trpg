"""S2-1：信念状态（`trpg/models/belief.py`）与五点契约的回归入口。

五点契约逐点钉住：

1. **模型**：`SelfBelief` / `Suspicion` / `Mindset` 的字段与校验（依据必填、概率有界）；
2. **StateDelta 白名单**：`characters.<id>.mindset` 可写，其余越界仍拒；
3. **prompt 注入**：缺省**不注入**；填了才注入，且只注入本人视角；
4. **文档**：字段集与白名单由 `tests/test_contract.py` 对齐知识库契约；
5. **字段集测试**：`CharacterRuntime` 字段集含 `mindset`。

外加一条纪律：**框架零世界词**——标签只是不透明串，词表来自世界包。
"""

from __future__ import annotations

import pytest

from trpg.engine import knowledge as knowledge_mod
from trpg.engine import state_merge
from trpg.models import CharacterRuntime, DeltaChange, GameState, Mindset, SelfBelief, StateDelta, Suspicion
from trpg.models.belief import belief_label_vocab, check_labels
from trpg.models.enums import DeltaOp
from support.builders import make_state


def _state_with_character() -> GameState:
    """一个带角色的最小 state（缺省没有任何信念）。"""
    state = make_state()
    state.characters["chr_a"] = CharacterRuntime(hp=5, sanity=5)
    return state


# ── 1. 模型 ──────────────────────────────────────────────────

def test_self_belief_is_a_probability_not_a_flag():
    belief = SelfBelief(label="alpha", confidence=0.62)
    assert belief.label == "alpha"
    assert belief.confidence == pytest.approx(0.62)


def test_confidence_must_be_a_probability():
    for bad in (-0.01, 1.01):
        with pytest.raises(Exception):
            SelfBelief(label="alpha", confidence=bad)
    with pytest.raises(Exception):
        Suspicion(target="t", confidence=1.5, reason="因为")


def test_suspicion_reason_is_required():
    """C-1 的核心：没有依据的怀疑度在 schema 层就被拒。"""
    with pytest.raises(Exception):
        Suspicion(target="t1", confidence=0.5, reason="")
    with pytest.raises(Exception):
        Suspicion(target="t1", confidence=0.5, reason="   ")
    with pytest.raises(Exception):
        Suspicion(target="", confidence=0.5, reason="他跳票")
    ok = Suspicion(target="t1", confidence=0.5, reason="他跳票")
    assert ok.reason == "他跳票"


def test_mindset_is_empty_by_default():
    mindset = Mindset()
    assert mindset.is_empty() is True
    assert mindset.suspicions == {}
    assert mindset.self_belief is None


def test_runtime_mindset_defaults_to_none():
    """缺省为空 = 提示词与改造前逐字节一致。"""
    rt = CharacterRuntime(hp=5, sanity=5)
    assert rt.mindset is None


def test_suspicions_are_keyed_by_target():
    mindset = Mindset(
        suspicions={
            "chr_a": Suspicion(target="chr_a", confidence=0.4, reason="他昨夜不在"),
            "chr_b": Suspicion(target="chr_b", confidence=0.2, reason="发言绕"),
        }
    )
    assert mindset.targets() == ("chr_a", "chr_b")
    assert mindset.is_empty() is False


# ── 2. 框架零世界词：标签词表来自世界包 ──────────────────────

class _Pack:
    """假的规则包：只提供世界包声明的标签词表。"""

    def __init__(self, labels: dict[str, str]):
        self.camp_labels = labels


def test_label_vocab_comes_from_world_pack():
    pack = _Pack({"beta": "乙", "alpha": "甲"})
    assert belief_label_vocab(pack) == ("alpha", "beta")
    # 世界包没声明阵营 → 空词表（不校验，不逼传统世界填阵营信念）
    assert belief_label_vocab(_Pack({})) == ()


def test_check_labels_rejects_labels_outside_vocab():
    mindset = Mindset(self_belief=SelfBelief(label="alpha", confidence=0.5))
    assert check_labels(mindset, ("alpha", "beta")) == []
    errors = check_labels(mindset, ("beta",))
    assert errors and "alpha" in errors[0]
    # 空词表 = 不校验
    assert check_labels(mindset, ()) == []


def test_framework_source_has_no_world_faction_words():
    """框架里不许出现具体阵营名（check_generic 之外的第二道保险）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "trpg" / "models" / "belief.py").read_text(
        encoding="utf-8"
    )
    for bad in ("狼", "村民", "悍跳", "屠边", "银水"):
        assert bad not in src, f"框架层不许出现世界词：{bad}"


# ── 3. StateDelta 白名单 ─────────────────────────────────────

def test_delta_whitelist_allows_mindset():
    joined = " ".join(p.pattern for p in state_merge._ALLOWED_PATTERNS)
    assert "mindset" in joined, "白名单缺 characters.<id>.mindset"


def test_delta_accepts_mindset_and_keeps_it():
    from trpg.models import DeltaChange, StateDelta
    from trpg.models.enums import DeltaOp

    state = _state_with_character()
    delta = StateDelta(
        changes=[
            DeltaChange(
                path="characters.chr_a.mindset",
                op=DeltaOp.set,
                value={
                    "self_belief": {"label": "alpha", "confidence": 0.5},
                    "suspicions": {
                        "chr_b": {"target": "chr_b", "confidence": 0.3, "reason": "他昨夜不在"}
                    },
                },
            )
        ]
    )
    new = state_merge.apply_delta(state, delta)
    mindset = new.characters["chr_a"].mindset
    assert isinstance(mindset, Mindset)
    assert mindset.self_belief.label == "alpha"
    assert mindset.suspicions["chr_b"].reason == "他昨夜不在"
    assert new.version == state.version + 1


def test_delta_rejects_mindset_without_reason():
    """没有依据的怀疑度，StateDelta 层也要拒（不是只有 schema 层拒）。"""
    from trpg.models import DeltaChange, StateDelta
    from trpg.models.enums import DeltaOp

    state = _state_with_character()
    delta = StateDelta(
        changes=[
            DeltaChange(
                path="characters.chr_a.mindset",
                op=DeltaOp.set,
                value={"suspicions": {"chr_b": {"target": "chr_b", "confidence": 0.3}}},
            )
        ]
    )
    with pytest.raises(state_merge.DeltaValidationError):
        state_merge.apply_delta(state, delta)


# ── 4. prompt 注入 ───────────────────────────────────────────

def test_mindset_is_not_injected_by_default():
    """缺省不注入：没有信念 = 提示词一个字都不多。"""
    rt = CharacterRuntime(hp=5, sanity=5)
    assert knowledge_mod.render_mindset(None, lambda cid: cid) == ()
    assert knowledge_mod.render_mindset(Mindset(), lambda cid: cid) == ()
    assert rt.mindset is None


def test_mindset_renders_into_prompt_lines():
    mindset = Mindset(
        self_belief=SelfBelief(label="alpha", confidence=0.6),
        suspicions={
            "chr_b": Suspicion(target="chr_b", label="beta", confidence=0.35, reason="他昨夜不在")
        },
    )
    lines = knowledge_mod.render_mindset(mindset, lambda cid: "乙")
    text = "\n".join(lines)
    assert "alpha" in text and "60%" in text
    assert "乙" in text and "beta" in text and "35%" in text
    assert "他昨夜不在" in text, "依据必须出现在提示词里"


def test_knowledge_section_includes_mindset_only_when_filled():
    """Knowledge 视图：填了才出现这一段。"""
    empty = knowledge_mod.Knowledge(enabled=True, sections=("mindset",))
    assert "你认为自己" not in empty.render()

    filled = knowledge_mod.Knowledge(
        enabled=True,
        sections=("mindset",),
        mindset=("- 你认为自己是：alpha（把握 60%）",),
    )
    assert "你认为自己" in filled.render()
