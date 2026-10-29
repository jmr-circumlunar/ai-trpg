"""S2-4：结构化发言 + 票理由 + 发言与票对齐。

钉住四件事：

1. **产出**：发言 schema 有**可选**字段（立场 / 怀疑对象 / 依据 / 欺骗类型），缺省空 = 旧行为不变；
2. **消费**：投票记录带**可选** `reason`（旧记录逐字节不变）；
3. **对齐**：投票阶段**消费发言里的怀疑**（模型声明优先，否则用信念里最可疑的人），并能查「说的和投的」是否一致；
4. **缺省不变**：没有理由/怀疑时，回执里的票与改造前完全一致。
"""

from __future__ import annotations

import pytest

from trpg.agents.schemas import AgentTurnOut, MindsetOut, VoteChoiceOut
from trpg.engine.phases.collective import VoteRecord, alignment
from trpg.models import PlayerAction


# ── 1. 产出：结构化字段（可选）───────────────────────────────

def test_agent_turn_out_has_optional_speech_fields():
    out = AgentTurnOut(type="final", action="发言")
    assert out.stance == ""
    assert out.suspect == ""
    assert out.evidence == ""
    assert out.deception == ""


def test_player_action_parses_speech_fields():
    action = PlayerAction.from_raw(
        "chr_a",
        {
            "action": "我认为乙可疑",
            "stance": "alpha",
            "suspect": "chr_b",
            "evidence": "他昨夜不在",
            "deception": "mislead",
        },
    )
    assert action.stance == "alpha"
    assert action.suspect == "chr_b"
    assert action.evidence == "他昨夜不在"
    assert action.deception == "mislead"


def test_player_action_defaults_unchanged():
    """旧输入（没有这些字段）→ 逐字节一致的行为。"""
    action = PlayerAction.from_raw("chr_a", {"action": "按兵不动"})
    assert action.stance == "" and action.suspect == ""
    assert action.evidence == "" and action.deception == ""


def test_mindset_out_schema_exists():
    """信念的结构化输出 schema（M12）：怀疑必须带依据。"""
    out = MindsetOut.model_validate(
        {
            "self_belief": {"label": "alpha", "confidence": 0.6},
            "suspicions": [
                {"target": "chr_b", "confidence": 0.3, "reason": "他昨夜不在"}
            ],
        }
    )
    assert out.self_belief["label"] == "alpha"
    assert out.suspicions[0]["reason"] == "他昨夜不在"


def test_vote_choice_out_schema_exists():
    out = VoteChoiceOut.model_validate({"target": "chr_b", "reason": "他昨夜不在"})
    assert out.target == "chr_b"
    assert out.reason == "他昨夜不在"


# ── 2. 消费：票带理由（旧记录兼容）──────────────────────────

def test_vote_record_carries_reason_and_suspect():
    vote = VoteRecord(voter="chr_a", target="chr_b", reason="他昨夜不在", suspect="chr_b")
    data = vote.as_dict()
    assert data["reason"] == "他昨夜不在"
    assert data["suspect"] == "chr_b"


def test_old_vote_record_is_byte_identical():
    """旧票（无理由、无怀疑）→ 回执里的票与改造前逐字节一致。"""
    vote = VoteRecord(voter="chr_a", target="chr_b")
    assert vote.as_dict() == {
        "voter": "chr_a",
        "target": "chr_b",
        "round": 1,
        "kind": "vote",
    }


# ── 3. 对齐：说的怀疑与投的人 ───────────────────────────────

def test_alignment_reports_match_and_mismatch():
    votes = [
        VoteRecord(voter="chr_a", target="chr_b", suspect="chr_b"),
        VoteRecord(voter="chr_c", target="chr_d", suspect="chr_b"),
    ]
    rows = alignment(votes)
    assert rows[0]["aligned"] is True
    assert rows[1]["aligned"] is False
    assert rows[1]["declared"] == "chr_b" and rows[1]["voted"] == "chr_d"


def test_alignment_skips_votes_without_declaration():
    assert alignment([VoteRecord(voter="chr_a", target="chr_b")]) == []


def test_vote_consumes_suspicion_from_belief():
    """投票阶段消费**发言里的怀疑**：模型没声明就用信念里最可疑的人，并取其依据。"""
    from trpg.engine.loop import GameLoop

    loop = _loop_with_belief()
    suspect = loop._top_suspicion("chr_a")
    assert suspect == "chr_b"
    reason = loop._suspicion_reason("chr_a", "chr_b")
    assert reason == "他昨夜不在"


def _loop_with_belief():
    from trpg.engine.loop import GameLoop
    from trpg.models import CharacterRuntime, Mindset, SelfBelief, Suspicion
    from trpg.rules.loader import Rules

    loop = GameLoop.__new__(GameLoop)
    state = _state()
    loop.state = state
    loop.rules = Rules()
    runtime = state.characters["chr_a"]
    runtime.mindset = Mindset(
        self_belief=SelfBelief(label="alpha", confidence=0.6),
        suspicions={
            "chr_b": Suspicion(
                target="chr_b", confidence=0.7, reason="他昨夜不在"
            ),
            "chr_c": Suspicion(target="chr_c", confidence=0.2, reason="他发言绕"),
        },
    )
    return loop


def _state():
    from trpg.models import CharacterRuntime
    from support.builders import make_state

    state = make_state()
    state.characters["chr_a"] = CharacterRuntime(hp=5, sanity=5)
    state.characters["chr_b"] = CharacterRuntime(hp=5, sanity=5)
    state.characters["chr_c"] = CharacterRuntime(hp=5, sanity=5)
    return state


# ── 4. 框架零世界词 ────────────────────────────────────────

def test_framework_has_no_world_faction_words():
    """新增的 schema 不许出现世界词（类别词表归世界包）。

    只查本步**新增**的 schema 文件：`collective.py` 的历史 docstring 里已有
    「狼人内部」这类描述性文字，不在本次改动范围（`check_generic` 才是全仓口径）。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    src = (root / "trpg" / "agents" / "schemas.py").read_text(encoding="utf-8")
    for bad in ("狼", "村民", "悍跳", "屠边", "银水"):
        assert bad not in src, f"schema 不许出现世界词：{bad}"
