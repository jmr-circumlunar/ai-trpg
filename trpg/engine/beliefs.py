"""M13 · 信念的跨回合累积与失败回落。

三件事（对应 S2-2 的三条实现约束）：

1. **旧信念整体进 prompt**：模型这一回合拿到的不是「从零再想一遍」，
   而是「我上轮怎么想的 + 这轮新发生了什么」——旧信念由 `knowledge.render_mindset`
   渲染进提示词（缺省为空 = 不注入）。
2. **增量更新**：模型返回的是**完整的新信念**（它在旧信念上做了增量推理），
   所以这里是**整包替换**，不是逐字段合并。
3. **失败即保留**：结构化输出解析失败 / 校验不过 → **返回旧信念**并记错，
   绝不返回空信念（C-3：返回空 = agent 失忆，而外面只看得出「变笨了」）。

存储在 `agent_beliefs` 表（`trpg/storage/beliefs.py`）；本模块只管业务规则。

doc: 03-模块/M13-回合编排/00-模块总述.md
module: M13
layer: L4
"""

from __future__ import annotations

from typing import Any

from ..models.belief import Mindset, check_labels


def restore_beliefs(state: Any, stored: dict[str, Any] | None) -> Any:
    """把存下来的**旧信念**回填进 state（重启 / 读档后不丢记忆）。

    `stored` 是 `{角色 id: mindset dict}`；state 里没有该角色就跳过。
    回填失败（结构不合法）就**保留原来的**（空或旧值），不抛错。
    """
    if not stored:
        return state
    characters = getattr(state, "characters", None)
    if not characters:
        return state
    for actor_id, raw in stored.items():
        runtime = characters.get(str(actor_id))
        if runtime is None:
            continue
        try:
            runtime.mindset = Mindset.model_validate(raw)
        except Exception:  # noqa: BLE001
            continue  # 存坏了就当没有——旧信念比坏信念重要
    return state


def update_mindset(
    old: Any,
    parsed: Any,
    vocab: tuple[str, ...] | list[str] | None = None,
) -> tuple[Any, str]:
    """**增量更新**信念：成功返回新信念，失败返回**旧信念**。

    返回 `(mindset, error)`：
    - `parsed` 为 None / 不是 dict / 校验不过 / 标签不在世界包词表 → `error` 非空，
      `mindset` 是 `old`（原样保留，不清零、不半截）；
    - 正常 → `error` 为空，`mindset` 是新的完整信念。

    旧信念可以是 `Mindset`、dict 或 None（None = 之前没有信念）。
    """
    if parsed is None:
        return old, "信念更新缺失：保留旧信念"
    if hasattr(parsed, "model_dump"):
        raw = parsed.model_dump()
    elif isinstance(parsed, dict):
        raw = dict(parsed)
    else:
        return old, f"信念更新格式非法：{type(parsed).__name__}（保留旧信念）"

    try:
        new = Mindset.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        return old, f"信念校验失败：{str(exc)[:200]}（保留旧信念）"

    errors = check_labels(new, vocab)
    if errors:
        return old, "；".join(errors) + "（保留旧信念）"

    return new, ""


def beliefs_of(state: Any) -> dict[str, Any]:
    """state 里所有角色当前的信念（写库用）。"""
    out: dict[str, Any] = {}
    for actor_id, runtime in (getattr(state, "characters", None) or {}).items():
        mindset = getattr(runtime, "mindset", None)
        if mindset is not None:
            out[str(actor_id)] = mindset
    return out


    for actor_id, runtime in (getattr(state, "characters", None) or {}).items():
        mindset = getattr(runtime, "mindset", None)
        if mindset is not None:
            out[str(actor_id)] = mindset
    return out


# ── 概率信念四步（S2-3）────────────────────────────────────────
#
# 先验 → 硬化 → 微调 → 守恒重归一（外加跨轮平滑）。
# **具体「什么算已确认」由世界包声明**（`knowledge.yaml: belief.confirmed`），
# 框架只提供四个通用槽位——不写任何世界语义（`check_generic` 守这条）。
# 参数全部来自 `rules/belief.yaml`（数值真相源），代码里只读不算。

_DEFAULT_PARAMS: dict[str, Any] = {
    "prior": {"floor": 0.0},
    "harden": {"enemy_confidence": 1.0, "ally_confidence": 0.0},
    "adjust": {"max_delta": 0.3, "min": 0.0, "max": 1.0},
    "smooth": {"weight": 0.6},
    "conservation": {"epsilon": 0.0001, "equal_share_fallback": True},
}


def _params(params: dict[str, Any] | None) -> dict[str, Any]:
    """取参数：调用方给的优先，否则用仓库默认那份 `rules/belief.yaml`。"""
    if params:
        return params
    try:
        from ..rules.loader import load_belief_rules

        return load_belief_rules()
    except Exception:  # noqa: BLE001
        return _DEFAULT_PARAMS


def enemy_count_from_state(
    state: Any, actor_id: str, hostile_camps: tuple[str, ...] | list[str] | None
) -> int:
    """**剩余敌数** = 存活角色里「阵营属于敌对阵营」的人数。

    「哪些阵营是敌对」由**世界包声明**（`knowledge.yaml: belief.hostile_camps`），
    框架只比阵营 id，不解释阵营含义。
    """
    hostile = {str(x) for x in (hostile_camps or ())}
    if not hostile:
        return 0
    from ..models.state import is_alive

    count = 0
    for cid, runtime in (getattr(state, "characters", None) or {}).items():
        if str(cid) == str(actor_id):
            continue
        if not is_alive(runtime):
            continue
        if str(getattr(runtime, "camp", "") or "") in hostile:
            count += 1
    return count


def compute_beliefs(
    *,
    candidates: list[str] | tuple[str, ...],
    enemy_count: int,
    confirmed: dict[str, Any] | None = None,
    adjustments: dict[str, float] | None = None,
    prior: dict[str, float] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """概率信念四步：返回 `{目标 id: {confidence, reason, sources}}`。

    - `candidates`：可怀疑对象（存活、且未被世界包声明为已确认）；
    - `enemy_count`：剩余敌数（由 `enemy_count_from_state` 按世界包声明算出）；
    - `confirmed`：`{目标 id: {"stance": "enemy"|"ally", "reason": "…", "source": "…"}}`
      ——**由世界包声明**（框架不写规则）；
    - `adjustments`：本轮微调（只对不确定项生效）；
    - `prior`：上一轮的怀疑度（跨轮平滑用）。
    """
    cfg = _params(params)
    harden_cfg = cfg.get("harden") or {}
    adjust_cfg = cfg.get("adjust") or {}
    smooth_cfg = cfg.get("smooth") or {}
    conserve_cfg = cfg.get("conservation") or {}
    prior_floor = float((cfg.get("prior") or {}).get("floor", 0.0) or 0.0)
    enemy_conf = float(harden_cfg.get("enemy_confidence", 1.0) or 0.0)
    ally_conf = float(harden_cfg.get("ally_confidence", 0.0) or 0.0)
    max_delta = float(adjust_cfg.get("max_delta", 0.0) or 0.0)
    adj_min = float(adjust_cfg.get("min", 0.0) or 0.0)
    adj_max = float(adjust_cfg.get("max", 1.0) or 1.0)
    smooth_w = float(smooth_cfg.get("weight", 1.0) or 0.0)
    epsilon = float(conserve_cfg.get("epsilon", 0.0) or 0.0)
    equal_fallback = bool(conserve_cfg.get("equal_share_fallback", True))

    pool = [str(x) for x in (candidates or ())]
    confirmed = dict(confirmed or {})
    adjustments = dict(adjustments or {})
    prior = {str(k): float(v or 0.0) for k, v in (prior or {}).items()}

    out: dict[str, dict[str, Any]] = {}
    # ── ② 硬化：世界包声明的已确认项直接取 0/1，不参与其余步骤 ──
    confirmed_enemies = 0
    for target, item in confirmed.items():
        stance = str((item or {}).get("stance") or "")
        reason = str((item or {}).get("reason") or "").strip()
        source = str((item or {}).get("source") or "").strip()
        if stance == "enemy":
            confidence = enemy_conf
            confirmed_enemies += 1
        elif stance == "ally":
            confidence = ally_conf
        else:
            continue
        out[str(target)] = {
            "confidence": confidence,
            "reason": reason or "已确认",
            "sources": [f"confirmed:{source or stance}"],
        }

    uncertain = [t for t in pool if t not in out]
    # ── ① 先验：剩余敌数 / 未知候选数 ──
    remaining_enemies = max(0, int(enemy_count or 0) - confirmed_enemies)
    base = (remaining_enemies / len(uncertain)) if uncertain else 0.0
    base = max(base, prior_floor)

    values: dict[str, float] = {}
    sources: dict[str, list[str]] = {}
    for target in uncertain:
        # ── ③ 微调：只对不确定项，且偏移有上限 ──
        delta = float(adjustments.get(target, 0.0) or 0.0)
        delta = max(-max_delta, min(max_delta, delta))
        adjusted = min(adj_max, max(adj_min, base + delta))
        # ── 跨轮平滑：即时判断 × weight + 长期信任 × (1 − weight) ──
        last = prior.get(target, adjusted)
        smoothed = smooth_w * adjusted + (1.0 - smooth_w) * last
        values[target] = smoothed
        marks = ["prior"]
        if delta:
            marks.append("adjust")
        if target in prior:
            marks.append("smooth")
        sources[target] = marks

    # ── ④ 守恒重归一：未确认项之和 = 剩余敌数 ──
    total = sum(values.values())
    if uncertain:
        if total <= epsilon:
            if equal_fallback and remaining_enemies > 0:
                share = remaining_enemies / len(uncertain)
                for target in uncertain:
                    values[target] = share
        else:
            scale = remaining_enemies / total
            for target in uncertain:
                values[target] = min(1.0, max(0.0, values[target] * scale))

    for target in uncertain:
        confidence = round(float(values.get(target, 0.0)), 6)
        marks = sources.get(target, [])
        reason = "先验 %.2f" % base if "prior" in marks else ""
        if "adjust" in marks:
            reason = ("%s；微调 %+.2f" % (reason, float(adjustments.get(target, 0.0) or 0.0))).strip("；")
        if "smooth" in marks:
            reason = ("%s；跨轮平滑" % reason).strip("；")
        out[target] = {
            "confidence": confidence,
            "reason": reason or "先验",
            "sources": marks,
        }
    return out


__all__ = [
    "beliefs_of",
    "compute_beliefs",
    "enemy_count_from_state",
    "restore_beliefs",
    "update_mindset",
]
