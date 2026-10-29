"""角色卡写入的**唯一入口**（局外成长专用）。

为什么单独一个模块：[[07-AI项目/AI跑团/02.9-代码架构约束|02.9 架构约束]] 禁止清单第 3 条
禁止「绕过 state_merge 直接改 `card.ext`」，只留了两个例外：

1. 身份 `grants` 的授权写入 → `trpg/rules/identity.py`
2. **局外成长（点数换来的技能 / 天赋 / 痕迹）** → 本模块

例外必须有边界：所有对 `card.ext` 的写入都在这两个模块里，别的模块一律只读卡。
这样「谁改了角色」是可审计的（查这两个文件即可），而不是散落全局。

doc: 03-模块/M10-成长/00-模块总述.md
module: M10
layer: L3
"""

from __future__ import annotations


def _talent_label(pack, target: str) -> str:
    """天赋 id → 本地名字（名字在世界包里，代码不存）。"""
    for t in getattr(pack, "talent_pool", []) or []:
        if str(t.get("id")) == str(target):
            return str(t.get("label") or target)
    return str(target)


def apply_skill(card, target: str, amount: int = 1) -> str:
    """技能 +N（买来的技能进 `ext.skills`）。"""
    skills = dict(card.ext.skills or {})
    skills[target] = int(skills.get(target) or 0) + int(amount)
    card.ext.skills = skills
    return f"{target} +{amount}"


def apply_talent(card, pack, target: str) -> str:
    """加一个天赋标签（去重）。"""
    label = _talent_label(pack, target)
    if label not in (card.ext.talents or []):
        card.ext.talents = [*card.ext.talents, label]
    return f"天赋：{label}"


def apply_trait(card, target: str, effect: str = "旧伤", count: int = 1) -> str:
    """加 count 条痕迹（scars）。"""
    card.ext.scars = [
        *(card.ext.scars or []),
        *({"kind": target, "effect": effect} for _ in range(max(1, int(count)))),
    ]
    return f"痕迹：{target}"


def apply_item(card, target: str, count: int = 1) -> str:
    """把携带的物品（**只带 id**）记进卡的携带清单。

    局外带进局的物品不带数值实例——数值由局内代码掷定，
    这里只记「带了什么」，符合 carry 的既定规则。
    """
    name = str(target or "").strip()
    if not name:
        return ""
    carried = list(getattr(card.ext, "carried", None) or [])
    for _ in range(max(0, int(count))):
        if name not in carried:
            carried.append(name)
    card.ext.carried = carried
    return name


def apply_path(card, pack, target: str) -> str:
    """授予身份路径（写 `card.ext.path`；路径本身是内容，id 由世界包给）。"""
    name = str(target or "").strip()
    if not name:
        return ""
    card.ext.path = name
    return name


def count_carried(card, target: str) -> int:
    """卡上带着多少个某物品（用于带入时只补差额）。"""
    name = str(target or "").strip()
    if not name:
        return 0
    return sum(1 for x in (getattr(card.ext, "carried", None) or []) if str(x) == name)


def count_traits(card, kind: str) -> int:
    """卡上已有多少条该类痕迹（carry 去重用）。"""
    return sum(1 for sc in (card.ext.scars or []) if str(sc.get("kind")) == str(kind))
