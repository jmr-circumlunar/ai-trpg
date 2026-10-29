import pytest

from trpg.engine.state_merge import DeltaValidationError, apply_delta
from trpg.models import DeltaChange, StateDelta
from trpg.models.enums import DeltaOp
from support.builders import make_card, make_state

CARDS = {"chr_test01": make_card()}


def delta(*changes, turn: int = 1) -> StateDelta:
    return StateDelta(turn=turn, changes=[DeltaChange(**c) for c in changes])


def test_normal_hp_sub():
    state = make_state()
    new = apply_delta(state, delta({"path": "characters.chr_test01.hp", "op": DeltaOp.sub, "value": 2}), CARDS)
    assert new.characters["chr_test01"].hp == 9
    assert new.version == state.version + 1


def test_hp_below_zero_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "characters.chr_test01.hp", "op": DeltaOp.sub, "value": 99}), CARDS)


def test_hp_drop_over_60pct_rejected():
    # hp_max=11 -> 允许最大变化 6.6，掉 8 应被拒
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "characters.chr_test01.hp", "op": DeltaOp.sub, "value": 8}), CARDS)


def test_tension_jump_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "tension", "op": DeltaOp.add, "value": 30}), CARDS)


def test_version_not_writable():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "version", "op": DeltaOp.set, "value": 99}), CARDS)


def test_world_not_writable():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "world.era", "op": DeltaOp.set, "value": "modern"}), CARDS)


def test_stage_cannot_skip():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "stage", "op": DeltaOp.add, "value": 2}), CARDS)


def test_stage_plus_one_ok():
    state = make_state()
    new = apply_delta(state, delta({"path": "stage", "op": DeltaOp.add, "value": 1}), CARDS)
    assert new.stage == state.stage + 1


def test_gold_negative_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(make_state(), delta({"path": "party.gold", "op": DeltaOp.sub, "value": 100}), CARDS)


def test_flag_add_ok():
    new = apply_delta(make_state(), delta({"path": "party.flags", "op": DeltaOp.add, "value": "heard_bell"}), CARDS)
    assert "heard_bell" in new.party.flags


def test_failed_delta_does_not_mutate_original():
    state = make_state()
    before = state.model_dump()
    with pytest.raises(DeltaValidationError):
        apply_delta(state, delta({"path": "party.gold", "op": DeltaOp.sub, "value": 100}), CARDS)
    assert state.model_dump() == before


def test_scene_move_ok():
    new = apply_delta(
        make_state(),
        delta({"path": "scene.location_id", "op": DeltaOp.set, "value": "loc_b"}),
        CARDS,
    )
    assert new.scene.location_id == "loc_b"


# ── quests：按 id 合并（GM 只会给 {id, progress}）─────────────
def _state_with_quest():
    from trpg.models import Quest

    state = make_state()
    state.quests = [Quest(id="q_main", title="主线", status="active", progress=0)]
    return state


def test_quest_partial_update_keeps_title():
    state = _state_with_quest()
    new = apply_delta(
        state,
        delta({"path": "quests", "op": DeltaOp.set, "value": [{"id": "q_main", "progress": 2}]}),
        CARDS,
    )
    assert new.quests[0].title == "主线"
    assert new.quests[0].progress == 2


def test_quest_status_update_ok():
    state = _state_with_quest()
    new = apply_delta(
        state,
        delta({"path": "quests", "op": DeltaOp.set, "value": [{"id": "q_main", "status": "done"}]}),
        CARDS,
    )
    assert new.quests[0].status == "done"
    assert new.quests[0].progress == 0


def test_new_quest_without_title_falls_back_to_id():
    """新任务缺 title 时用 id 兜底：不让文案缺失拖死整回合的状态变更。"""
    new = apply_delta(
        _state_with_quest(),
        delta({"path": "quests", "op": DeltaOp.set, "value": [{"id": "q_new", "progress": 1}]}),
        CARDS,
    )
    added = next(q for q in new.quests if q.id == "q_new")
    assert added.title == "q_new"
    assert added.progress == 1


def test_quest_add_op_also_merges():
    """GM 用 op: add 追加单条任务时，也走按 id 合并（不产生无 title 的裸 dict）。"""
    new = apply_delta(
        _state_with_quest(),
        delta(
            {
                "path": "quests",
                "op": DeltaOp.add,
                "value": {"id": "q_main", "progress": 3},
            }
        ),
        CARDS,
    )
    assert new.quests[0].progress == 3
    assert new.quests[0].title == "主线"


def test_new_quest_with_title_appended():
    new = apply_delta(
        _state_with_quest(),
        delta(
            {
                "path": "quests",
                "op": DeltaOp.set,
                "value": [{"id": "q_new", "title": "支线", "progress": 0}],
            }
        ),
        CARDS,
    )
    assert [q.id for q in new.quests] == ["q_main", "q_new"]


def test_quest_subpath_updates_one_item():
    """GM 写 quests.q_x（点号子路径）时，只改那一条，不整包回滚。"""
    new = apply_delta(
        _state_with_quest(),
        delta({"path": "quests.q_main", "op": DeltaOp.set, "value": {"progress": 4}}),
        CARDS,
    )
    assert new.quests[0].progress == 4
    assert new.quests[0].title == "主线"


def test_quest_subpath_add_accumulates_progress():
    new = apply_delta(
        _state_with_quest(),
        delta({"path": "quests.q_main", "op": DeltaOp.add, "value": 2}),
        CARDS,
    )
    assert new.quests[0].progress == 2


def test_quest_count_capped():
    """任务栏最多 8 条：GM 重复换 id 时丢弃新增，保住已有任务。"""
    from trpg.models import Quest

    state = make_state()
    state.quests = [Quest(id=f"q{i}", title=f"任务{i}") for i in range(8)]
    new = apply_delta(
        state,
        delta({"path": "quests", "op": DeltaOp.set, "value": [{"id": "q_dup", "title": "重复"}]}),
        CARDS,
    )
    assert len(new.quests) == 8
    assert all(q.id != "q_dup" for q in new.quests)


def test_quests_single_dict_accepted():
    """GM 只给一个任务对象时（不是列表）也按单条更新处理。"""
    new = apply_delta(
        _state_with_quest(),
        delta({"path": "quests", "op": DeltaOp.set, "value": {"id": "q_main", "progress": 5}}),
        CARDS,
    )
    assert new.quests[0].progress == 5


def test_quests_bad_item_type_rejected():
    with pytest.raises(DeltaValidationError):
        apply_delta(
            _state_with_quest(),
            delta({"path": "quests", "op": DeltaOp.set, "value": ["不是任务对象"]}),
            CARDS,
        )


# ── conditions：封闭词表（叙事短语不进硬状态）──────────────────
VOCAB = ["dying", "流血", "暴露"]


def _conds(state, **kw):
    return state.characters["chr_test01"].conditions


def test_condition_vocab_drops_narrative_phrase():
    new = apply_delta(
        make_state(),
        delta(
            {
                "path": "characters.chr_test01.conditions",
                "op": DeltaOp.add,
                "value": "登记受阻",
            }
        ),
        CARDS,
        condition_vocab=VOCAB,
    )
    assert _conds(new) == []


def test_condition_vocab_keeps_known_word():
    new = apply_delta(
        make_state(),
        delta({"path": "characters.chr_test01.conditions", "op": DeltaOp.add, "value": "流血"}),
        CARDS,
        condition_vocab=VOCAB,
    )
    assert _conds(new) == ["流血"]


def test_condition_vocab_add_dedupes():
    new = apply_delta(
        make_state(),
        delta(
            {"path": "characters.chr_test01.conditions", "op": DeltaOp.add, "value": "流血"},
            {"path": "characters.chr_test01.conditions", "op": DeltaOp.add, "value": "流血"},
        ),
        CARDS,
        condition_vocab=VOCAB,
    )
    assert _conds(new) == ["流血"]


def test_condition_vocab_set_filters():
    new = apply_delta(
        make_state(),
        delta(
            {
                "path": "characters.chr_test01.conditions",
                "op": DeltaOp.set,
                "value": ["暴露", "乱写的词"],
            }
        ),
        CARDS,
        condition_vocab=VOCAB,
    )
    assert _conds(new) == ["暴露"]


def test_no_vocab_keeps_legacy_behaviour():
    """没给词表（旧调用方 / 单测）时不过滤，保持向后兼容。"""
    new = apply_delta(
        make_state(),
        delta(
            {"path": "characters.chr_test01.conditions", "op": DeltaOp.add, "value": "随便写"}
        ),
        CARDS,
    )
    assert _conds(new) == ["随便写"]
