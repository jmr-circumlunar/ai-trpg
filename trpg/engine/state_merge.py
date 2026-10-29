"""StateDelta 合并与校验。

GM 只能输出增量，且只能改白名单路径；越界一律整包回滚。
这是「单一真相源」在数据层的落地，也是 S0 最该先测的东西。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from ..models import CharacterCard, GameState, Mindset, StateDelta
from ..models.enums import DeltaOp
from ..clock import core as clock_mod
from ..growth import identity

_MAX_TENSION_DELTA = 20
_MAX_HP_RATIO = 0.6
# 任务栏上限：GM 会同一件事换 id 重报（实测 3 回合冒出 5 条、其中 2 条重复）。
# 超出就丢弃新增，保住已有任务，而不是整包回滚。
_MAX_QUESTS = 8

# 白名单分两层（C50）：
#   base        —— 不属于任何「可关能力」的基础字段，恒可写
#   capability  —— 关闭对应系统后不可写（否则「关了还在跑」）
# 全开时两层合起来的**可写路径集合**与改造前逐条等价（golden 快照守）

_BASE_PATTERNS = [
    # 理智/状态/钱/补给：不属于某个可关系统
    re.compile(r"^characters\.[A-Za-z0-9_]+\.(sanity|conditions|gold|supplies)$"),
    re.compile(r"^party\.(gold|supplies|flags|reputation)$"),
    # 时间只能通过 clock.advance（分钟数）前进，day/hour/minute 不允许直接 set。
    # 天气是 clock 的字段，不单独挂 weather 系统（该系统未实现，缺省不可开）
    re.compile(r"^clock\.(advance|weather)$"),
    re.compile(r"^tension$"),
    re.compile(r"^stage$"),
    # `scene.exits` 已移除：出口由代码按地图生成，GM 不得凭空造路
    re.compile(r"^scene\.(present_npcs|description)$"),
    re.compile(r"^quests$"),
    # 信念状态（S2-1）：角色自己的心态（自我信念 + 怀疑度）。
    # 整包替换（set）：怀疑度是**成套**的（自我信念 + 若干怀疑），
    # 拆成子路径逐条写会拼出「有怀疑没有依据」的半截状态。
    re.compile(r"^characters\.[A-Za-z0-9_]+\.mindset$"),
    # 单条任务子路径（GM 实测会写 quests.q_xxx）：按 id 合并到那一条
    re.compile(r"^quests\.[^\s]+$"),
]

_CAPABILITY_PATTERNS: dict[str, list[re.Pattern]] = {
    "combat": [
        re.compile(r"^characters\.[A-Za-z0-9_]+\.hp$"),
        # 敌人整包（只由 loop 的战斗结算写入；prompt 不向 GM 暴露这条路径）
        re.compile(r"^enemies$"),
    ],
    "equipment": [re.compile(r"^characters\.[A-Za-z0-9_]+\.inventory$")],
    "death": [re.compile(r"^characters\.[A-Za-z0-9_]+\.position$")],
    "identity": [re.compile(r"^characters\.[A-Za-z0-9_]+\.path$")],
    "map": [re.compile(r"^scene\.location_id$")],
}

# 兼容导出：老调用点/测试仍可直接拿到「全开」的完整集合
_ALLOWED_PATTERNS = _BASE_PATTERNS + [
    pattern for patterns in _CAPABILITY_PATTERNS.values() for pattern in patterns
]


@lru_cache(maxsize=64)
def _patterns_for(enabled: frozenset[str] | None) -> tuple[re.Pattern, ...]:
    """按能力集算白名单；`None` = 全开（等价于改造前行为）。"""
    if enabled is None:
        return tuple(_ALLOWED_PATTERNS)
    patterns = list(_BASE_PATTERNS)
    for capability, extra in _CAPABILITY_PATTERNS.items():
        if capability in enabled:
            patterns.extend(extra)
    return tuple(patterns)


def _capabilities_of(pack) -> frozenset[str] | None:
    """本局开启的系统集合；没有 pack（离线调用/单测）时返回 None = 全开。

    装配失败时退化为全开而不是抛错：回合不能因为一个配置问题整局卡死，
    配置错误由 `check_world` 在开局前拦。
    """
    if pack is None:
        return None
    try:
        from ..rulespack import assemble

        return assemble(pack).enabled
    except Exception:  # noqa: BLE001
        return None

_FORBIDDEN_PATTERN = re.compile(r"^(version|session_id|world|schema_version)(\.|$)")


class DeltaValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("StateDelta 校验失败：" + "; ".join(errors))


def _path_allowed(path: str, enabled: frozenset[str] | None = None) -> bool:
    if _FORBIDDEN_PATTERN.match(path):
        return False
    return any(p.match(path) for p in _patterns_for(enabled))


def _get(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part)
    return cur


def _set(obj: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part)
    last = parts[-1]
    if isinstance(cur, dict):
        cur[last] = value
    else:
        setattr(cur, last, value)


def _merge_quests(
    cur: list, value: Any
) -> tuple[list | None, list[str]]:
    """按 id 合并任务列表，返回 (新列表, 错误)。

    规则（写入 02.1 §五）：
    - 给了已存在的 id：只覆盖给出的字段（`{id, progress}` 是合法的最小更新）
    - 给了新 id：`title` 缺失时用 id 兜底（任务名是叙事产物，不该让整回合陪葬）
    - 不是列表 / 缺 id：拒绝
    """
    errors: list[str] = []
    if not isinstance(value, list):
        return None, ["quests 必须是任务对象列表"]

    by_id: dict[str, dict] = {}
    for item in cur or []:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        if data.get("id"):
            by_id[str(data["id"])] = data

    order: list[str] = []
    for q in cur or []:
        qid = str(q.get("id")) if isinstance(q, dict) else str(getattr(q, "id", ""))
        if qid:
            order.append(qid)

    for item in value:
        if isinstance(item, dict):
            data = dict(item)
        elif hasattr(item, "model_dump"):
            data = item.model_dump()
        else:
            errors.append(f"quests 项类型非法：{type(item).__name__}")
            continue

        qid = str(data.get("id") or "").strip()
        if not qid:
            errors.append("quests 项缺少 id")
            continue

        base = by_id.get(qid)
        if base is None:
            if not str(data.get("title") or "").strip():
                data["title"] = qid  # 兜底：宁可名字难看，也不要整包回滚
            data.setdefault("status", "active")
            data.setdefault("progress", 0)
            by_id[qid] = data
            order.append(qid)
            continue

        merged = dict(base)
        merged.update({k: v for k, v in data.items() if v is not None})
        by_id[qid] = merged

    out = [by_id[qid] for qid in order if qid in by_id]
    if len(out) > _MAX_QUESTS:
        # 保留已有任务（顺序靠前），丢弃多出来的新增
        out = out[:_MAX_QUESTS]
    return out, errors


def apply_delta(
    state: GameState,
    delta: StateDelta,
    cards: dict[str, CharacterCard] | None = None,
    clock_cfg: dict | None = None,
    pack=None,
    hp_cap: float | None = _MAX_HP_RATIO,
    condition_vocab: list[str] | None = None,
) -> GameState:
    """应用增量并校验，返回新 state（version+1）。失败抛 DeltaValidationError。

    `pack` 给了才校验身份闸门（世界包 `paths.yaml`）；不给则只要求 `path` 非空，
    保持旧世界与单测兼容。

    `hp_cap` 是单回合 hp 变化上限（占 hp_max 比例）。默认 0.6 用来防 GM 乱写；
    代码内部结算战斗伤害时传 `None`（一刀砍掉半管血是正常的）。
    """
    cards = cards or {}
    clock_cfg = clock_cfg or {}
    vocab = {str(x) for x in (condition_vocab or [])}
    step_min = int(clock_cfg.get("min_step_minutes", 5))
    step_max = int(clock_cfg.get("max_step_minutes", 480))
    clock_total = 0
    errors: list[str] = []

    # 1) 路径白名单（按本局能力裁剪：关闭的系统其字段不可写）
    enabled = _capabilities_of(pack)
    for ch in delta.changes:
        if not _path_allowed(ch.path, enabled):
            errors.append(f"路径不允许修改：{ch.path}")
    if errors:
        raise DeltaValidationError(errors)

    old_hp = {cid: rt.hp for cid, rt in state.characters.items()}
    old_tension = state.tension
    old_stage = state.stage

    new = state.model_copy(deep=True)

    # 2) 逐条应用
    for ch in delta.changes:
        # 时间推进是虚拟路径，需要特殊处理（跨天进位 + 档位校验）
        if ch.path == "clock.advance":
            try:
                minutes = int(ch.value or 0)
            except (TypeError, ValueError):
                errors.append("clock.advance 必须是整数分钟")
                continue
            if minutes < step_min:
                errors.append(f"clock.advance 小于最小档 {step_min} 分钟（收到 {minutes}）")
                continue
            if minutes > step_max:
                errors.append(f"clock.advance 超过单回合上限 {step_max} 分钟（收到 {minutes}）")
                continue
            clock_total += minutes
            if clock_total > step_max:
                errors.append(f"单回合累计推进超过 {step_max} 分钟")
                continue
            d, h, m = clock_mod.advance(
                new.clock.day, new.clock.hour, new.clock.minute, minutes
            )
            new.clock.day, new.clock.hour, new.clock.minute = d, h, m
            continue

        # 身份只能整值替换：add/sub 会拼出 "freshman_exec_member" 这种鬼东西
        if ch.path.endswith(".path") and ch.op != DeltaOp.set:
            errors.append(f"身份只能用 set 变更：{ch.path}（收到 {ch.op.value}）")
            continue

        # quests 子路径（GM 实测会写 quests.q_xxx）必须在取 cur 之前拦截：
        # quests 是列表，按 "quests.<id>" 取不到东西。
        if ch.path.startswith("quests."):
            qid = ch.path.split(".", 1)[1]
            value = ch.value
            if isinstance(value, dict):
                patch = dict(value)
            elif ch.op == DeltaOp.add and isinstance(value, (int, float)):
                existing = next(
                    (q for q in _get(new, "quests") if str(getattr(q, "id", "")) == qid),
                    None,
                )
                patch = {"progress": int(getattr(existing, "progress", 0) or 0) + int(value)}
            else:
                patch = {"progress": value}
            merged, qerrors = _merge_quests(_get(new, "quests"), [{"id": qid, **patch}])
            errors.extend(qerrors)
            if merged is not None:
                _set(new, "quests", merged)
            continue

        try:
            cur = _get(new, ch.path)
        except (AttributeError, KeyError):
            errors.append(f"路径不存在：{ch.path}")
            continue

        # quests 是实体列表：GM 常见两种写法（整表 set / 追加单条 add），一律按 id 合并。
        # 直接整包替换会让"漏写 title"把整个回合的状态变更一起拖死。
        if ch.path == "quests":
            items = ch.value if isinstance(ch.value, list) else [ch.value]
            merged, qerrors = _merge_quests(cur, items)
            errors.extend(qerrors)
            if merged is not None:
                _set(new, ch.path, merged)
            continue

        # 信念状态（S2-1）：整包替换，但要**过模型校验**——
        # 怀疑度必须带依据（reason 必填），缺依据的怀疑度在这里就被拒。
        # 直接 set 原始 dict 会绕过 pydantic（嵌套实例默认不复验），所以显式 coerce。
        if ch.path.endswith(".mindset"):
            try:
                value = None if ch.value is None else Mindset.model_validate(ch.value)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"mindset 非法：{str(exc)[:200]}")
                continue
            _set(new, ch.path, value)
            continue

        # conditions 是硬状态，只收封闭词表内的词。
        # GM 常把叙事短语（「登记受阻」）当状态塞进来，会让状态栏越积越长且永不清除。
        if ch.path.endswith(".conditions") and vocab:
            incoming = ch.value if isinstance(ch.value, list) else [ch.value]
            kept = [str(x) for x in incoming if str(x) in vocab]
            if ch.op == DeltaOp.set:
                _set(new, ch.path, kept)
            elif ch.op == DeltaOp.add:
                current = cur if isinstance(cur, list) else []
                _set(new, ch.path, list(dict.fromkeys([*current, *kept])))
            else:
                errors.append(f"conditions 不支持 sub：{ch.path}")
            continue

        if ch.op == DeltaOp.set:
            _set(new, ch.path, ch.value)
        elif ch.op == DeltaOp.add:
            if isinstance(cur, list):
                # GM 常把单值写成数组（[["exposed"]] 的坑），这里统一摊平
                if isinstance(ch.value, list):
                    _set(new, ch.path, [*cur, *ch.value])
                else:
                    _set(new, ch.path, [*cur, ch.value])
            else:
                _set(new, ch.path, (cur or 0) + ch.value)
        elif ch.op == DeltaOp.sub:
            if not isinstance(cur, int):
                errors.append(f"sub 只能用于数值：{ch.path}")
            else:
                _set(new, ch.path, cur - int(ch.value))

    # 3b) 身份闸门：path 变更必须在世界包内，且满足 requirements
    for cid, rt in new.characters.items():
        prev_rt = state.characters.get(cid)
        if prev_rt is not None and rt.path == prev_rt.path:
            continue
        prev_path = (prev_rt.path if prev_rt is not None else "") or (
            cards[cid].ext.path if cid in cards else ""
        )
        errors.extend(
            identity.check_path_change(
                pack, new, cards.get(cid), rt.path, cid=cid, current=prev_path
            )
        )

    # 3) 边界与幅度
    for cid, rt in new.characters.items():
        card = cards.get(cid)
        hp_max = card.ext.hp_max if card else None

        if rt.hp < 0:
            errors.append(f"{cid}.hp < 0")
        if hp_max is not None:
            if rt.hp > hp_max:
                errors.append(f"{cid}.hp > hp_max({hp_max})")
            prev = old_hp.get(cid)
            if (
                hp_cap is not None
                and prev is not None
                and abs(rt.hp - prev) > hp_max * hp_cap
            ):
                errors.append(f"{cid}.hp 单回合变化超过 {int(hp_cap * 100)}%")
        if rt.sanity < 0:
            errors.append(f"{cid}.sanity < 0")

    if not 0 <= new.tension <= 100:
        errors.append(f"tension 越界：{new.tension}")
    if abs(new.tension - old_tension) > _MAX_TENSION_DELTA:
        errors.append(f"tension 单回合变化超过 ±{_MAX_TENSION_DELTA}")

    if new.stage not in (old_stage, old_stage + 1):
        errors.append(f"stage 只能 +1，当前 {old_stage} -> {new.stage}")

    if new.party.gold < 0:
        errors.append("party.gold 不得为负")
    if new.party.supplies < 0:
        errors.append("party.supplies 不得为负")

    # 角色级钱与补给：必须显式检查。
    # 注意 `GameState.model_validate(raw)` **不会**兜住这个——
    # raw 里装的是已构造好的 CharacterRuntime 实例，pydantic 默认不重新校验嵌套实例，
    # 所以 `gold: int = Field(ge=0)` 在这里形同虚设（实测扣成 -984 也不报）。
    for cid, rt in new.characters.items():
        if rt.gold < 0:
            errors.append(f"{cid}.gold 不得为负")
        if rt.supplies < 0:
            errors.append(f"{cid}.supplies 不得为负")

    # 4) 结构复校验：任何 set/add 进来的值都必须符合 schema
    #    （例如 quests 被替换成 dict 列表时，这里会转回 Quest 或直接报错）
    try:
        # 用 __dict__ 而不是 model_dump()：dump 会对 quests/enemies 里的 dict 触发序列化警告，
        # 掩盖真正的类型问题
        raw = {key: getattr(new, key) for key in GameState.model_fields}
        new = GameState.model_validate(raw)
    except Exception as exc:
        errors.append(f"state 结构非法：{str(exc)[:600]}")

    if errors:
        raise DeltaValidationError(errors)

    new.version = state.version + 1
    return new
