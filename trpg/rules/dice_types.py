"""M02 规则 · 骰型注册表：**名字与参数在 yaml，机制在代码**。

为什么要有注册表：以前 `DiceEngine.roll()` 只会解析 `NdS±M`，玩法想换个骰子
（池运算 / 成功计数 / 爆炸）就得改代码。现在「骰型」是注册表里的一条声明：

```yaml
types:
  pool: {kind: pool, count: 3, keep_mode: highest, keep: 3}
```

- **参数**（数量、保留几颗、阈值、爆炸轮数上限）来自 `rules/dice.yaml` → 数值真相源；
- **机制**（怎么掷、怎么保留、怎么计）在本文件 → 框架能力，与玩法无关；
- **缺省零变化**：老路径 `DiceEngine.roll("2d6+1")` 一行未改，行为逐字节不变。

表达法参照 Roll20 / VTT 通行记法（`4d6kh3` 取最高 3 颗、`1d6!` 爆炸、
`2d6r>=5` 成功计数），但**不引第三方库**：那是表达法的参照，不是依赖。

doc: 03-模块/M02-规则与判定/00-模块总述.md
module: M02
layer: L1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


class DiceError(ValueError):
    """骰型参数不合法（名字没注册 / 数量越界 / 阈值越界）。"""


@dataclass
class DiceResult:
    """一次骰型的结果。

    `rolls` 是**掷出的原始面**（池运算里含被丢弃的那些），
    `total` 是该骰型定义的汇总值（基础骰=总和，池运算=保留之和，成功计数=成功次数）。
    `detail` 放机制特有的明细（保留了什么、炸了几次）。
    """

    rolls: list[int] = field(default_factory=list)
    total: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DiceType(Protocol):
    """骰型的最小契约：能被注册表按名字取出来、按参数掷出结果。"""

    name: str
    kind: str

    def roll(self, engine: Any, params: dict[str, Any]) -> DiceResult:
        """用 `engine`（提供 `d(sides)` 与随机状态）掷一次。"""
        ...


# ── 机制实现（kind → 函数）──────────────────────────────────
# 每个机制只负责「怎么掷、怎么汇总」；数量上限等护栏由 _limits 统一管。

def _check_count(count: int, limits: dict[str, Any]) -> int:
    """骰子颗数护栏：数量必须在 1..max_count 之间。"""
    max_count = int(limits.get("max_count", 20))
    value = int(count)
    if value < 1:
        raise DiceError(f"骰子数量必须 ≥ 1：{value}")
    if value > max_count:
        raise DiceError(f"骰子数量超过上限 {max_count}：{value}")
    return value


def _check_sides(sides: int, limits: dict[str, Any]) -> int:
    min_sides = int(limits.get("min_sides", 2))
    max_sides = int(limits.get("max_sides", 1000))
    value = int(sides)
    if value < min_sides:
        raise DiceError(f"骰面数必须 ≥ {min_sides}：{value}")
    if value > max_sides:
        raise DiceError(f"骰面数超过上限 {max_sides}：{value}")
    return value


def _roll_basic(engine: Any, params: dict[str, Any], limits: dict[str, Any]) -> DiceResult:
    """基础骰：掷 count 颗同面数骰，取总和（可带固定加值）。"""
    count = _check_count(int(params.get("count", 1)), limits)
    sides = _check_sides(int(params.get("sides", 6)), limits)
    modifier = int(params.get("modifier", 0) or 0)
    rolls = [engine.d(sides) for _ in range(count)]
    return DiceResult(rolls=rolls, total=sum(rolls) + modifier,
                      detail={"sides": sides, "modifier": modifier})


def _roll_pool(engine: Any, params: dict[str, Any], limits: dict[str, Any]) -> DiceResult:
    """池运算：掷 count 颗，保留 keep 颗（最高或最低），其余丢弃。"""
    count = _check_count(int(params.get("count", 3)), limits)
    sides = _check_sides(int(params.get("sides", 6)), limits)
    keep = int(params.get("keep", count))
    if keep < 1:
        raise DiceError(f"保留颗数必须 ≥ 1：{keep}")
    if keep > count:
        raise DiceError(f"保留颗数 {keep} 超过掷出颗数 {count}")
    mode = str(params.get("keep_mode", "highest")).lower()
    if mode not in ("highest", "lowest"):
        raise DiceError(f"keep_mode 只能是 highest / lowest：{mode}")

    rolls = [engine.d(sides) for _ in range(count)]
    ordered = sorted(rolls, reverse=(mode == "highest"))
    kept = ordered[:keep]
    dropped = ordered[keep:]
    return DiceResult(
        rolls=rolls,
        total=sum(kept),
        detail={"sides": sides, "keep_mode": mode, "kept": kept, "dropped": dropped},
    )


def _roll_success_count(engine: Any, params: dict[str, Any], limits: dict[str, Any]) -> DiceResult:
    """条件成功计数：结果 >= threshold 的骰子各记一次成功。"""
    count = _check_count(int(params.get("count", 3)), limits)
    sides = _check_sides(int(params.get("sides", 6)), limits)
    threshold = int(params.get("threshold", 5))
    if threshold < 1 or threshold > sides:
        raise DiceError(f"成功阈值必须落在 1..{sides}：{threshold}")

    rolls = [engine.d(sides) for _ in range(count)]
    successes = [r for r in rolls if r >= threshold]
    return DiceResult(
        rolls=rolls,
        total=len(successes),
        detail={"sides": sides, "threshold": threshold, "successes": successes},
    )


def _roll_explode(engine: Any, params: dict[str, Any], limits: dict[str, Any]) -> DiceResult:
    """爆炸骰：达到阈值就重掷并累加（可条件爆炸）。

    终止保证：单颗骰最多重掷 `max_rounds` 轮（yaml 可配，另有全局上限），
    绝不会无限循环 —— 这是必须有的护栏，不是可选项。
    """
    count = _check_count(int(params.get("count", 1)), limits)
    sides = _check_sides(int(params.get("sides", 6)), limits)
    raw_threshold = params.get("threshold", None)
    threshold = None if raw_threshold in (None, "", "null") else int(raw_threshold)
    if threshold is not None and (threshold < 1 or threshold > sides):
        raise DiceError(f"爆炸阈值必须落在 1..{sides}：{threshold}")
    global_cap = int(limits.get("max_explode_rounds", 50))
    max_rounds = max(1, min(int(params.get("max_rounds", global_cap)), global_cap))

    all_rolls: list[int] = []
    exploded = 0
    for _ in range(count):
        face = engine.d(sides)
        all_rolls.append(face)
        # 阈值为空 = 掷出最大值即爆炸；否则 >= 阈值即爆炸
        trigger = (face == sides) if threshold is None else (face >= threshold)
        rounds = 0
        while trigger and rounds < max_rounds:
            rounds += 1
            face = engine.d(sides)
            all_rolls.append(face)
            exploded += 1
            trigger = (face == sides) if threshold is None else (face >= threshold)
    return DiceResult(
        rolls=all_rolls,
        total=sum(all_rolls),
        detail={"sides": sides, "threshold": threshold, "exploded": exploded},
    )


_ROLLERS: dict[str, Callable[[Any, dict[str, Any], dict[str, Any]], DiceResult]] = {
    "basic": _roll_basic,
    "pool": _roll_pool,
    "success_count": _roll_success_count,
    "explode": _roll_explode,
}


# ── 注册表 ─────────────────────────────────────────────────

@dataclass
class RegisteredDiceType:
    """注册表里的一条骰型：名字 + 机制 + 参数（参数来自 yaml）。"""

    name: str
    kind: str
    label: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)

    def roll(self, engine: Any, overrides: dict[str, Any] | None = None) -> DiceResult:
        roller = _ROLLERS.get(self.kind)
        if roller is None:
            raise DiceError(f"未知骰型机制：{self.kind}（骰型 {self.name}）")
        params = dict(self.params)
        params.update(overrides or {})
        return roller(engine, params, self.limits)


_REGISTRY: dict[str, RegisteredDiceType] = {}


def register_dice_type(name: str, dice_type: RegisteredDiceType) -> RegisteredDiceType:
    """注册一个骰型（同名覆盖，便于世界包扩展）。"""
    key = str(name).strip()
    if not key:
        raise DiceError("骰型名字不能为空")
    _REGISTRY[key] = dice_type
    return dice_type


def get_dice_type(name: str) -> RegisteredDiceType:
    """按名字取骰型；没注册就报错（不做静默回落，配置错误要看得见）。"""
    key = str(name or "").strip()
    if key not in _REGISTRY:
        raise DiceError(f"未注册的骰型：{key}（已注册：{', '.join(sorted(_REGISTRY)) or '无'}）")
    return _REGISTRY[key]


def registered_names() -> list[str]:
    return sorted(_REGISTRY)


def clear_registry() -> None:
    """清空注册表（测试用；运行时不要调用）。"""
    _REGISTRY.clear()


def build_registry(config: dict[str, Any]) -> dict[str, RegisteredDiceType]:
    """按 `rules/dice.yaml` 的内容构建注册表（并注册到全局表）。

    - `types` 段：名字 → `{kind, label, ...参数}`；
    - `limits` 段：全局护栏（数量/面数/爆炸轮数上限）。
    """
    cfg = dict(config or {})
    limits = dict(cfg.get("limits") or {})
    built: dict[str, RegisteredDiceType] = {}
    for name, spec in (cfg.get("types") or {}).items():
        spec = dict(spec or {})
        kind = str(spec.get("kind") or "basic")
        if kind not in _ROLLERS:
            raise DiceError(f"骰型 {name} 声明了未知机制：{kind}")
        params = {k: v for k, v in spec.items() if k not in ("kind", "label")}
        dice_type = RegisteredDiceType(
            name=str(name),
            kind=kind,
            label=str(spec.get("label") or ""),
            params=params,
            limits=limits,
        )
        built[str(name)] = register_dice_type(str(name), dice_type)
    return built


def bands_for(result: int, bands: list[dict[str, Any]] | None) -> str:
    """结果档位映射：命中返回档位名，没命中返回空串（不猜）。"""
    for band in bands or []:
        lo = int(band.get("min", 0))
        hi = int(band.get("max", 10 ** 9))
        if lo <= int(result) <= hi:
            return str(band.get("name") or "")
    return ""
