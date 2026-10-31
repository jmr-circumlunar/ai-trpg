"""S7-3：同时行动 + free 双硬闸 + 竞价 + 意图裁决。

出口判据逐条对应：
1. **同时行动收齐后统一结算**（且顺序稳定可复现）；
2. **双闸生效**：轮数闸与 token 闸**各自单独触发**（硬断言）；
3. **竞价分配发言权**；
4. **意图动作裁决**（合法 / 非法 / 无校验器）；
5. **缺省行为不变**：不是 simultaneous 时行动者顺序与原来一致。
"""

from __future__ import annotations

from trpg.engine.scheduler import (
    FreeGate,
    allocate_speech_order,
    is_collected,
    judge_intent,
    resolve_simultaneous,
    select_active_players,
)


# ── ① 同时行动 ──────────────────────────────────────────────
def test_select_active_players_default_keeps_order():
    actors = [("c1", object()), ("c2", object()), ("c3", object())]
    assert select_active_players(actors) == ["c1", "c2", "c3"], "缺省行为不变"


def test_select_active_players_for_simultaneous():
    actors = [("c1", object()), ("c2", object())]
    got = select_active_players(actors, mode="simultaneous")
    assert got == ["c1", "c2"]
    assert len(got) == 2, "同时行动的一组人就是全部行动者（一起行动）"


def test_resolve_requires_all_collected():
    """**收齐**才允许结算：部分收齐不算。"""
    active = ["c1", "c2"]
    assert is_collected({"c1": 1}, active) is False
    assert is_collected({"c1": 1, "c2": 2}, active) is True
    assert is_collected({}, []) is True, "没有同时行动 → 不设收齐门槛"


def test_resolve_simultaneous_is_stable():
    actions = {"c1": "a", "c2": "b", "c3": "c"}
    first = resolve_simultaneous(actions, seed="s1", turn=2)
    second = resolve_simultaneous(actions, seed="s1", turn=2)
    assert [cid for cid, _ in first] == [cid for cid, _ in second], "同 seed 必须同序"
    assert {cid for cid, _ in first} == {"c1", "c2", "c3"}


def test_resolve_simultaneous_empty():
    assert resolve_simultaneous({}, seed="s") == []


# ── ② free 双硬闸 ───────────────────────────────────────────
def test_rounds_gate_triggers_alone():
    gate = FreeGate.from_config({"mode": "free", "max_rounds": 2})
    assert gate.should_stop() is False
    gate.consume_round()
    assert gate.should_stop() is False
    gate.consume_round()
    assert gate.should_stop() is True, "轮数闸应触发"
    assert gate.stop_reason().startswith("rounds:")


def test_token_gate_triggers_alone():
    gate = FreeGate.from_config({"mode": "free", "token_budget": 100})
    gate.consume_tokens(50)
    assert gate.should_stop() is False, "token 闸不该提前触发"
    gate.consume_tokens(50)
    assert gate.should_stop() is True, "token 闸应触发"
    assert gate.stop_reason().startswith("tokens:")


def test_gate_disabled_when_not_free():
    gate = FreeGate.from_config({"mode": "sequential", "max_rounds": 1, "token_budget": 1})
    gate.consume_round()
    gate.consume_tokens(99)
    assert gate.should_stop() is False, "非 free 模式不该有闸"


def test_gate_state_round_trips_for_checkpoint():
    """闸的已用量要能进 checkpoint（恢复后从已用预算继续）。"""
    gate = FreeGate.from_config({"mode": "free", "max_rounds": 5, "token_budget": 500})
    gate.consume_round()
    gate.consume_tokens(120)
    restored = FreeGate.from_state(gate.state())
    assert restored.rounds_used == 1
    assert restored.tokens_used == 120
    assert restored.max_rounds == 5 and restored.token_budget == 500


# ── ③ 竞价分配发言权 ─────────────────────────────────────────
def test_bidding_highest_first():
    order = allocate_speech_order({"a": 1.0, "b": 5.0, "c": 3.0}, rule="highest")
    assert order[0] == "b", "出价高者先说"


def test_bidding_lowest_first():
    order = allocate_speech_order({"a": 1.0, "b": 5.0, "c": 3.0}, rule="lowest")
    assert order[0] == "a", "反向规则：出价低者先说"


def test_bidding_ties_are_stable():
    bids = {"a": 2.0, "b": 2.0, "c": 2.0}
    first = allocate_speech_order(bids, seed="s", turn=1)
    second = allocate_speech_order(bids, seed="s", turn=1)
    assert first == second, "同分也要稳定（同 seed 同序）"


def test_bidding_empty():
    assert allocate_speech_order({}) == []


# ── ④ 意图动作裁决 ──────────────────────────────────────────
def test_intent_accepted_when_valid():
    ok, reason = judge_intent({"action": "look"}, lambda i: (True, "合法"))
    assert ok is True and reason == "合法"


def test_intent_rejected_when_invalid():
    ok, reason = judge_intent({"action": "fly"}, lambda i: (False, "不允许"))
    assert ok is False and reason == "不允许"


def test_intent_without_validator_is_rejected():
    """没有校验器 → 不接受（引擎不自己发明合法性）。"""
    ok, reason = judge_intent({"action": "look"}, None)
    assert ok is False
    assert "校验器" in reason


def test_intent_none_is_rejected():
    ok, _ = judge_intent(None, lambda i: (True, ""))
    assert ok is False


def test_validator_exception_is_reported_not_raised():
    def boom(_):
        raise RuntimeError("坏了")

    ok, reason = judge_intent({"a": 1}, boom)
    assert ok is False
    assert "校验异常" in reason


# ── ⑤ 缺省行为不变 + 声明 ────────────────────────────────────
def test_ruleset_declares_comms_gates():
    """双闸与竞价的**数值在 yaml**（代码不写死）。"""
    import yaml

    from trpg.rules.loader import Rules

    cfg = Rules(".").ruleset.get("comms") or {}
    assert cfg.get("mode") == "free"
    assert int(cfg.get("max_rounds") or 0) > 0, "轮数上限必须在声明里"
    assert int(cfg.get("token_budget") or 0) > 0, "token 上限必须在声明里"
    assert "bidding" in cfg


def test_manifest_does_not_claim_unimplemented_order():
    """未接线的 order 取值**不进清单**（写了不生效是最坏的体验）。

    `simultaneous` 的语义与纯逻辑已就绪（`engine/scheduler.py`），
    但主路径还没接线 → 按 manifest 的规矩不进枚举，接线后再加回来。
    """
    from trpg.rulespack.manifest import turn_flow_defaults

    assert "simultaneous" not in turn_flow_defaults["order"].values
    assert "dex" in turn_flow_defaults["order"].values


def test_turn_context_has_active_players():
    from trpg.engine.phases.context import TurnContext

    ctx = TurnContext(turn=1)
    assert ctx.active_players == [], "缺省空 = 不是同时行动"
