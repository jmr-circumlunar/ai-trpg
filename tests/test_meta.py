"""三期：局外成长（通用机制 + 世界包价目表）。

核心契约（知识库 `过程记录/方案/04-三期-局外成长插件化.md`）：
- 数据层只存抽象 `point`；本地名字由世界包给；
- 能买什么、多少钱，**完全由世界包决定**；
- 点数可跨世界累计，但**不能跨世界套现**（定价与消耗各世界独立）；
- 花费先校验后扣点，失败不扣。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trpg.growth import earn as meta_earn
from trpg.growth import shop as meta_shop
from trpg.growth.carry import apply_carry
from trpg.growth.hooks import MetaHookError, load_hooks, resolve_spend_targets
from trpg.growth.wallet import Wallet
from trpg.rules.loader import Rules
from support.builders import make_card
from trpg.world import load_world

ROOT = Path(__file__).resolve().parents[1]
LONGZU = ROOT / "worlds" / "longzu"
IRONVALE = ROOT / "worlds" / "ironvale"
SKILLS = {"观察", "交涉", "意志", "撬锁", "潜行", "医疗", "追踪", "生存"}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    from trpg.storage import db

    db.init_db(c)
    yield c
    c.close()


# ── 钱包 ────────────────────────────────────────────────────
def test_wallet_add_and_spend(conn):
    w = Wallet(conn)
    w.add("point", 5, reason="测试")
    assert w.balance() == 5
    assert w.spend("point", 3, reason="买") is True
    assert w.balance() == 2
    assert w.ledger()[0]["delta"] == -3


def test_wallet_spend_insufficient_does_not_deduct(conn):
    w = Wallet(conn)
    w.add("point", 2)
    assert w.spend("point", 5) is False
    assert w.balance() == 2, "余额不足不能扣点"


def test_wallet_entries_and_ledger(conn):
    w = Wallet(conn)
    w.add("point", 1, world_id="", reason="a")
    w.add("trait:trait_scars", 1, world_id="longzu", reason="b")
    keys = {(e["world_id"], e["key"]) for e in w.entries()}
    assert ("", "point") in keys
    assert ("longzu", "trait:trait_scars") in keys


# ── hooks ───────────────────────────────────────────────────
def test_hooks_loaded_from_world_pack():
    hooks = load_hooks(LONGZU, "longzu")
    assert hooks.currency_key == "point"
    assert hooks.currency_label == "龙血点"
    assert hooks.earn_for("ending") == 2
    assert hooks.earn_for("不存在的��件") == 0


def test_hooks_different_world_has_different_price_list():
    a = load_hooks(LONGZU, "longzu")
    b = load_hooks(IRONVALE, "ironvale")
    assert a.currency_label != b.currency_label, "本地货币名应该由世界包给"
    a_targets = {r["target"] for r in a.spend_rules()}
    b_targets = {r["target"] for r in b.spend_rules()}
    assert a_targets != b_targets, "两个世界的价目表应该不同"


def test_hooks_validate_rejects_unknown(tmp_path):
    bad = tmp_path / "meta_hooks.yaml"
    bad.write_text(
        "meta:\n  currency: {key: point}\n  earn:\n    - {event: 不存在的事件, amount: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(MetaHookError):
        load_hooks(tmp_path, "x")


# ── earn ────────────────────────────────────────────────────
def test_earn_uses_world_price_list(conn):
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    detail = meta_earn.apply_events(hooks, w, ["ending", "combat_won"], session_id="ses_x")
    assert sum(d["amount"] for d in detail) == 3  # 2 + 1
    assert w.balance() == 3
    assert detail[0]["currency"] == "龙血点"


def test_earn_unknown_event_gives_nothing(conn):
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    assert meta_earn.apply_events(hooks, w, ["没配的事件"]) == []
    assert w.balance() == 0


# ── spend ───────────────────────────────────────────────────
def _pack():
    return load_world("longzu", ROOT)


def test_spend_skill_raises_it(conn):
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    w.add("point", 10)
    card = make_card("chr_x")
    before = int(card.ext.skills.get("观察") or 0)
    res = meta_shop.purchase(hooks, w, _pack(), SKILLS, card, "skill", "观察")
    assert res["ok"] is True
    assert int(card.ext.skills.get("观察") or 0) == before + 1
    assert w.balance() == 10 - res["cost"]


def test_spend_without_points_fails_and_keeps_card(conn):
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    card = make_card("chr_x")
    res = meta_shop.purchase(hooks, w, _pack(), SKILLS, card, "skill", "观察")
    assert res["ok"] is False
    assert "点数不够" in res["error"]
    assert not card.ext.skills.get("观察"), "失败的购买不能改卡"


def test_spend_item_not_offered_by_world(conn):
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    w.add("point", 50)
    card = make_card("chr_x")
    res = meta_shop.purchase(hooks, w, _pack(), SKILLS, card, "skill", "医疗")
    # 医疗不在 longzu 的价目表里
    assert res["ok"] is False


def test_spend_max_limit(conn):
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    w.add("point", 100)
    card = make_card("chr_x")
    for _ in range(2):  # max=2
        assert meta_shop.purchase(hooks, w, _pack(), SKILLS, card, "skill", "观察")["ok"]
    res = meta_shop.purchase(hooks, w, _pack(), SKILLS, card, "skill", "观察")
    assert res["ok"] is False


def test_points_are_global_but_prices_are_per_world(conn):
    """同一份点数在两个世界买的东西不同（不能跨世界套现）。"""
    hooks_a = load_hooks(LONGZU, "longzu")
    hooks_b = load_hooks(IRONVALE, "ironvale")
    w = Wallet(conn)
    w.add("point", 6)

    card_a = make_card("chr_a")
    res_a = meta_shop.purchase(hooks_a, w, _pack(), SKILLS, card_a, "skill", "观察")
    assert res_a["ok"] is True  # longzu: 观察 3 点

    card_b = make_card("chr_b")
    res_b = meta_shop.purchase(hooks_b, w, _pack(), SKILLS, card_b, "skill", "撬锁")
    assert res_b["ok"] is True  # ironvale: 撬锁 2 点
    assert w.balance() == 6 - res_a["cost"] - res_b["cost"]


def test_spend_trait_counts_per_world(conn):
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    w.add("point", 20)
    card = make_card("chr_x")
    res = meta_shop.purchase(hooks, w, _pack(), SKILLS, card, "trait", "trait_scars")
    assert res["ok"] is True
    assert card.ext.scars
    assert w.get("trait:trait_scars", "longzu") == 1


# ── carry ───────────────────────────────────────────────────
def test_carry_injects_trait_into_new_card(conn):
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    w.add("trait:trait_scars", 2, world_id="longzu")
    card = make_card("chr_x")
    applied = apply_carry(hooks, w, card, _pack())
    assert applied and applied[0]["count"] == 2
    assert len(card.ext.scars) == 2


def test_carry_does_not_duplicate(conn):
    hooks = load_hooks(LONGZU, "longzu")
    w = Wallet(conn)
    w.add("trait:trait_scars", 2, world_id="longzu")
    card = make_card("chr_x")
    apply_carry(hooks, w, card, _pack())
    apply_carry(hooks, w, card, _pack())
    assert len(card.ext.scars) == 2, "重复开局不能重复注入"


# ── 价目表自检 ──────────────────────────────────────────────
def test_spend_targets_exist_in_world():
    hooks = load_hooks(LONGZU, "longzu")
    pack = _pack()
    items = resolve_spend_targets(hooks, pack, SKILLS)
    for item in items:
        assert item["ok"], f"{item['kind']}:{item['target']} 在本世界不存在"


def test_balance_takes_key_first(conn):
    """余额接口的参数顺序是 (key, world_id)——world_id 只能写关键字。"""
    w = Wallet(conn)
    w.add("point", 5)
    assert w.balance() == 5
    assert w.balance("point") == 5
    assert w.balance("point", world_id="") == 5
    assert w.balance("trait:trait_scars", world_id="longzu") == 0
