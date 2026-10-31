"""M18 能力清单（唯一真相）：框架对世界包公开的全部可配置项。

世界包作者只需要读这份清单（经 `tools/describe_capabilities.py` 导出成文档），
就知道「能开什么、能排哪些阶段、每个阶段能填什么参数、哪些字段可写」。

**这里只放元数据，不放行为**：
- 行为在 `assemble.py`（装配）、`predicates.py`（谓词函数）、`engine/pipeline.py`（阶段执行体）
- 清单与行为的一致性由 `tests/test_capability_manifest.py` 守

doc: 03-模块/M18-规则包与能力包/00-模块总述.md
module: M18
layer: L2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 元数据类型 ────────────────────────────────────────────────


@dataclass(frozen=True)
class ParamSpec:
    """阶段参数：世界包能填的字段。"""

    type: str  # str / int / bool / list / dict / enum
    default: Any = None
    values: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class PhaseKindSpec:
    """阶段种类：框架提供的 7 个通用执行体，世界包不能新增。"""

    kind: str
    summary: str
    params: dict[str, ParamSpec] = field(default_factory=dict)
    requires: tuple[str, ...] = ()
    precedes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SystemSpec:
    """系统开关：一个能力域是否启用。"""

    id: str
    status: str  # planned（已预留未实现）/ active / deprecated
    summary: str = ""
    required: bool = False
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    affects: tuple[str, ...] = ()


@dataclass(frozen=True)
class PredicateSpec:
    """阶段条件谓词：纯函数 + 依赖的系统。"""

    name: str
    needs: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class WinKindSpec:
    """胜负条件的一种判法（`world.yaml: win_condition.kind`）。

    与 `PhaseKindSpec` 同构：**清单在这里，行为在 `engine/ending.py`**。
    世界包只能从这份清单里选 kind 与参数，不能自造判法；
    `validate.py` 也只查这份清单，避免「校验放过、运行时静默」。
    """

    kind: str
    summary: str
    params: dict[str, ParamSpec] = field(default_factory=dict)
    # 需要角色卡（身份表）才能判：按「身份」分组的规则离不开它。
    # 调用方没传就直接抛错——不能伪装成「还没结束」。
    needs_cards: bool = False


#: 阶段声明上的**元键**：由调度器解释，不属于任何 kind 的参数表。
#:
#: 校验器（`validate`）与解析器（`pipeline`）**必须用同一份**。
#: 以前两处各写一套（校验侧是宽松超集），后果是：世界包写了个不认识参数，
#: `check_world` 通过、运行时 `parse_turn_flow` 抛错后**静默回落到默认流程**——
#: 作者以为配置生效了，实际跑的是另一套。这类「不报错的失效」比报错难查得多。
#:
#: `with` 是**谓词参数**（`{flag: burst}` / `{n: 3}` / `{stage: 2}` 这类）：
#: 谓词签名里的参数不放宽成 kind 参数，避免把「阶段参数」与「条件参数」混成一锅。
#:
#: `banner` 是**主持人播报台词**（"天黑请闭眼""狼人请睁眼"）：阶段开始时由框架原样
#: 发一条 `banner` 事件，前端当作主持人旁白。文案属世界包内容，框架不生成。
META_KEYS = frozenset({"id", "kind", "when", "break_if", "repeat", "with", "banner"})


@dataclass(frozen=True)
class ToolSpec:
    """Agent 工具：需要哪些系统才可见。

    `requires_comms` 表示「还需要世界开了交流（`turn_flow.yaml: comms.mode != none`）」。
    这条**必须声明在这里**：此前它只写在两处 `visible_tools` 的 `if name == "say"` 里，
    而 `assemble.tool_set()` 不知道这条规则，于是在没有交流的世界里仍返回 `say`——
    拿 `tool_set` 做权限校验就会漏。
    """

    name: str
    requires: tuple[str, ...]
    summary: str
    requires_comms: bool = False


@dataclass(frozen=True)
class UiBlockSpec:
    """前端区块：关闭依赖的系统即不渲染。"""

    sel: str
    requires: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class WorldFieldSpec:
    """世界包文件：必填/可选 + 缺省值。"""

    name: str
    required: bool
    default: Any
    summary: str


# ── 一、系统开关 ──────────────────────────────────────────────
#
# status 的口径：active = 代码已实现；planned = 已预留但未实现（开启须显式失败或降级）；
# deprecated = 保留兼容但新世界不应使用。
# `death` 不 requires `combat`：出局可以来自战斗、毒、投票或剧情，战斗只是其中一种。

systems: dict[str, SystemSpec] = {
    "time": SystemSpec(
        id="time",
        status="active",
        summary="回合时间推进与时段（晨/昼/昏/夜）",
        required=True,
        affects=("clock.*", "predicate:clock_phase"),
    ),
    "map": SystemSpec(
        id="map",
        status="active",
        summary="区域/地点、邻接移动与出口",
        affects=("scene.location_id", "scene.exits", "tool:look", "predicate:location_tag"),
    ),
    "identity": SystemSpec(
        id="identity",
        status="active",
        summary="身份与身份阶梯（可作身份牌）",
        affects=("characters.*.path", "prompt:path_catalog"),
    ),
    "equipment": SystemSpec(
        id="equipment",
        status="active",
        summary="装备与背包、成色与数值",
        affects=("characters.*.inventory", "ui:#profile-drawer"),
    ),
    "combat": SystemSpec(
        id="combat",
        status="active",
        summary="敌人申报、战斗轮与伤害结算",
        requires=("equipment",),
        affects=("enemies", "characters.*.hp", "ui:#hp-enemies"),
    ),
    "death": SystemSpec(
        id="death",
        status="active",
        summary="濒死/出局判定与出局后的表达限制",
        affects=("characters.*.position", "tool:say"),
    ),
    "judge": SystemSpec(
        id="judge",
        status="active",
        summary="判定与四档结果（关闭则世界不掷骰）",
        affects=("phase:resolve", "outcomes"),
    ),
    "long_memory": SystemSpec(
        id="long_memory",
        status="active",
        summary="长期记忆召回（向量 + 结构化）",
        affects=("tool:recall", "memory:vector"),
    ),
    "meta_growth": SystemSpec(
        id="meta_growth",
        status="active",
        summary="局外成长：点数、价目表与带入",
        affects=("ui:#meta-spend", "ui:#promote-list"),
    ),
    "events": SystemSpec(
        id="events",
        status="active",
        summary="事件种子与调度",
        affects=("seeds.yaml", "phase:event"),
    ),
    "immersion": SystemSpec(
        id="immersion",
        status="active",
        summary="沉浸规则与介入",
        affects=("rules:immersion",),
    ),
    "human_seat": SystemSpec(
        id="human_seat",
        status="active",
        summary="人类席位与等待/代打",
        affects=("ui:#human-panel", "predicate:has_human_seat"),
    ),
    "experience": SystemSpec(
        id="experience",
        status="active",
        summary="经验：agent 跨局积累的判断（程序记忆，固化进 prompt，不走检索）",
        affects=("prompt:experience", "table:agent_experience"),
    ),
    "weather": SystemSpec(
        id="weather",
        status="planned",
        summary="天气系统（已预留，未实现）",
        requires=("time",),
        affects=("clock.weather",),
    ),
}


# ── 二、阶段种类（7 个通用执行体） ────────────────────────────

# `order` 只列**已实现**的取值：`rotating` / `simultaneous` / `free` 属后续阶段，
# 以前写在枚举里但实现一律退化成 dex——世界包选了也看不出来（「写了不生效」）。
# 未实现就不该出现在清单里；等真的做了再加回来。
#
# S7-3：`simultaneous`（一组玩家同时行动，收齐后统一结算）的**语义与纯逻辑**
# 已在 `trpg/engine/scheduler.py` 就绪（boardgame.io 的 `activePlayers` 语义），
# 但**主路径尚未接线** → 按上面的规矩，**不进枚举**（接线后再加回来）。
_ORDER = ("dex", "fixed")
_VISIBILITY = ("previous", "none", "all", "public", "private", "group")

#: `turn_flow.yaml` 顶层 `declare:` 块：**action 阶段的缺省值**（C48）。
#:
#: 以前只在 `action.params.order` 的注释里承诺「缺省继承 `declare.order`」，
#: 而 `parse_turn_flow` 只读 `from` / `phases`——世界包写了也不生效（G6-D4）。
#: 现在清单、解析器、校验器共用这一份：写了就算数，写错就报错。
turn_flow_defaults: dict[str, ParamSpec] = {
    "order": ParamSpec("enum", "dex", _ORDER, "action 阶段的缺省行动顺序"),
    "visibility": ParamSpec("enum", "previous", _VISIBILITY, "action 阶段的缺省可见性"),
}

phase_kinds: dict[str, PhaseKindSpec] = {
    "narration": PhaseKindSpec(
        kind="narration",
        summary="生成/叙述当前场景（不产生行动与判定）",
        params={
            "source": ParamSpec("enum", "llm", ("llm", "world"), "叙述来源"),
            "style": ParamSpec("str", "", (), "额外风格约束"),
        },
    ),
    "action": PhaseKindSpec(
        kind="action",
        summary="角色各自行动：可定序、可分组、可限制相互可见",
        params={
            "order": ParamSpec("enum", "dex", _ORDER, "行动顺序；缺省继承 declare.order"),
            "visibility": ParamSpec(
                "enum", "previous", _VISIBILITY, "谁能看到谁的行动；缺省继承 declare.visibility"
            ),
            "groups": ParamSpec("list", [], (), "分组行动：by/value/visible_to/mode"),
            "judge": ParamSpec("bool", None, (), "本阶段是否判定；None=继承 systems.judge"),
            "fixed_sequence": ParamSpec("list", [], (), "order=fixed 时的顺序（也是「座位表」）"),
            # 标准回合制玩法里「发言从死者下一位开始、单双日方向相反」是通用需求，
            # 但具体谁是「下一位」只能由世界包给的座位表决定——框架不猜。
            "start_after": ParamSpec(
                "enum", "none", ("none", "last_death"), "order=fixed 时的起点：从本回合最后一名出局者的下一位开始"
            ),
            # 谁有资格在这个阶段行动（D6）：夜里不该让「没有夜间能力的人」也申报行动。
            "actors": ParamSpec(
                "enum",
                "all",
                ("all", "groups", "skill_holders"),
                "参与者：all=全部能动的人 / groups=只含 groups 里的组成员 / skill_holders=持有本时机技能的人",
            ),
            "direction": ParamSpec(
                "enum",
                "forward",
                ("forward", "reverse", "by_day_parity"),
                "order=fixed 时的绕行方向；by_day_parity=单数天正向、双数天反向",
            ),
        },
        precedes=("commit",),
    ),
    "collective": PhaseKindSpec(
        kind="collective",
        summary="集体决策：每人一票，代码计票（投票只是一种）",
        params={
            "rule": ParamSpec(
                "enum", "plurality", ("plurality", "majority", "weighted"), "计票规则"
            ),
            "tie": ParamSpec("enum", "random", ("runoff", "random", "none"), "平票处理"),
            "eligible": ParamSpec("enum", "alive", ("alive", "all"), "谁有投票资格"),
            "weight_by": ParamSpec("str", "", (), "加权票来源（如某属性）"),
            # 平票重投的轮数上限：只有 tie=runoff 时有意义。
            # 第一轮平票 → 只让平票者再被投一轮；再平票才无人出局。
            "rounds": ParamSpec("int", 1, (), "最多投几轮（平票重投用）"),
        },
    ),
    "apply": PhaseKindSpec(
        kind="apply",
        summary="把上一阶段的结果落实为状态变更",
        params={
            "target": ParamSpec(
                "enum",
                "collective_result",
                # `consensus_result` = 组内共议的结论（B66 狼群选刀口），
                # 以前实现了却没登记在枚举里——加取值校验后才暴露出来。
                ("collective_result", "consensus_result", "named"),
                "作用对象",
            ),
            "effect": ParamSpec(
                "enum", "eliminate", ("eliminate", "mark", "flag", "item_shift"), "效果类型"
            ),
            "payload": ParamSpec("dict", {}, (), "效果参数"),
            # 出局是否留遗言：标准信息对抗玩法里，出局那一刻的公开表态本身就是信息战。
            "last_words": ParamSpec("bool", False, (), "出局时是否留一句公开发言"),
            # 什么时候留：标准规则是「首夜死者 + 每次被投出局者」，夜间从第二夜起不再有。
            # 不放进 `when`：`when` 会跳掉「落实淘汰」本身，那就没人出局了。
            "last_words_when": ParamSpec(
                "enum", "always", ("always", "first_turn"), "遗言时机；first_turn=只在第 1 回合"
            ),
        },
    ),
    "check": PhaseKindSpec(
        kind="check",
        summary="条件求值：满足 break_if 则本回合后续阶段全部跳过",
        params={
            "when": ParamSpec("str", "always", (), "本阶段是否执行的谓词"),
            "break_if": ParamSpec("str", "", (), "命中则提前结束回合的谓词"),
            "repeat": ParamSpec("int", 1, (), "同回合内重复次数"),
        },
    ),
    "broadcast": PhaseKindSpec(
        kind="broadcast",
        summary="叙事 +（可选）代码公布：出局名单 / 平安夜",
        params={
            # 只留**真的实现了**的取值：以前还列着 `state_field`、`template`、`audience`，
            # 全都没有消费方——世界包写了也不生效（D5 的同一个坑）。
            "source": ParamSpec(
                "enum",
                "static",
                ("eliminated_this_turn", "static"),
                "代码公布什么：本回合出局名单（无出局则报平安夜）/ 只叙事",
            ),
        },
    ),
    "commit": PhaseKindSpec(
        kind="commit",
        summary="落库与收尾（状态合并、回执写入、摘要）",
        params={
            "persist": ParamSpec("bool", True, (), "是否落库"),
            "summary": ParamSpec("bool", True, (), "是否触发摘要"),
        },
        precedes=(),
    ),
    # 主动技能（A2）：世界包在 `skills.yaml` 里声明「谁有什么技能、什么时机、什么效果」，
    # 这个执行体只负责「按声明的时机，逐个有技能的人选一个技能并结算」。
    # 效果原语封闭在框架（见 `skill_effects`），世界包不能自定义脚本——方案 26 已否决
    # 「任意表达式」：那需要解释器 + 类型系统，而收益只是少写一个效果名。
    "skill": PhaseKindSpec(
        kind="skill",
        summary="按世界包声明的技能逐个结算（效果原语由框架提供）",
        params={
            "order": ParamSpec("enum", "fixed", _ORDER, "处理顺序；缺省按座位表"),
            "fixed_sequence": ParamSpec("list", [], (), "order=fixed 时的座位表"),
            # 同一回合里可能有两段技能窗口（例如白天与夜晚各一段）。
            # `window` 是**世界包自取的标签**：技能声明里的 `window` 与阶段参数对上才发动；
            # 技能不写 `window` = 任何窗口都能用。
            "window": ParamSpec("str", "", (), "本阶段处理哪个窗口的技能（空 = 不限）"),
        },
        precedes=("commit",),
    ),
}


# ── 三、阶段谓词（10 个纯函数） ──────────────────────────────

predicates: dict[str, PredicateSpec] = {
    "always": PredicateSpec("always", (), "每回合都执行"),
    "enemies_present": PredicateSpec(
        "enemies_present", ("combat",), "场上存在存活敌人"
    ),
    "in_combat": PredicateSpec("in_combat", ("combat",), "处于战斗轮（含对峙/追逃）"),
    "every_n": PredicateSpec("every_n", (), "每 N 回合执行一次（配 n）"),
    "stage_is": PredicateSpec("stage_is", (), "指定幕次（配 stage）"),
    "quest_active": PredicateSpec("quest_active", (), "指定任务进行中（配 quest）"),
    "location_tag": PredicateSpec("location_tag", ("map",), "当前地点带某 tag（配 tag）"),
    "flag_set": PredicateSpec("flag_set", (), "状态 flag 为真（配 flag）"),
    "has_human_seat": PredicateSpec(
        "has_human_seat", ("human_seat",), "本局有人类席位"
    ),
    "clock_phase": PredicateSpec("clock_phase", ("time",), "当前时段匹配（配 phase）"),
    # B57：胜负已定。它读 `state.winner`（由 evaluate_win 写入），不依赖任何系统开关，
    # 所以没有 camp 的世界里恒为假（等于「永不提前结束」）。
    "win_decided": PredicateSpec("win_decided", (), "胜负已定（state.winner 非空）"),
}


# ── 三·附、胜负条件（`win_condition.kind`，C58） ──────────────
#
# 四档都是「比较某组角色的出局情况或存活人数」，规则里不出现任何世界词汇；
# 阵营 id 与身份/职业 id 由世界包定义，框架不认识具体名字。
#
# `group_eliminated` 是「某类身份全灭」（屠边）：只按阵营判会把
# 「杀光某一类身份但同阵营其他身份还在」误判成没结束。
#
# `camp_eliminated` 用 `camp` 并反推胜方；`group_eliminated` 要显式 `winner`
# ——组可能跨阵营，反推不可靠。两种写法并存是**有意**的，不是漏统一。

_CAMP_PARAM = ParamSpec("str", "", (), "阵营 id（由世界包定义）")

win_kinds: dict[str, WinKindSpec] = {
    "all_eliminated": WinKindSpec("all_eliminated", "全员出局"),
    "camp_eliminated": WinKindSpec(
        "camp_eliminated", "指定阵营全部出局（判其余阵营胜）", {"camp": _CAMP_PARAM}
    ),
    "camp_superiority": WinKindSpec(
        "camp_superiority", "指定阵营存活人数 ≥ 其他阵营之和", {"camp": _CAMP_PARAM}
    ),
    "group_eliminated": WinKindSpec(
        "group_eliminated",
        "指定身份组全部出局（需身份表；空组不判）",
        {
            "group": ParamSpec("list", [], (), "身份/职业 id 列表"),
            "winner": ParamSpec("str", "", (), "命中时判胜的一方"),
            "label": ParamSpec("str", "", (), "回执里的称呼（可选）"),
        },
        needs_cards=True,
    ),
}


# ── 三·附二、主动技能的取值域（A2） ────────────────────────
#
# 世界包在 `skills.yaml` 里只能从这里选：触发时机、效果原语、可选中范围。
# 新增效果 = 机制变更（清单 + `loop._phase_skill` 分支 + 测试三处同步）。
# 为什么不让世界包写效果脚本：见 `skill` 阶段 kind 上的注释（方案 26 已否决任意表达式）。

skill_triggers: dict[str, str] = {
    "turn": "回合内的技能阶段（`skill` 执行体处理这一类）",
    "death": "出局那一刻触发（如开枪），不占回合阶段",
}

skill_effects: dict[str, str] = {
    "reveal_camp": "查看目标阵营，结果只写给施术者",
    "heal": "本回合刀杀不生效（目标免死）",
    "poison": "目标直接出局，且不留遗言、不触发出局技能",
    "shoot": "目标直接出局（由出局触发使用）",
    "self_eliminate": "施术者自己出局，并可写一个回合标记（自爆后跳过当日剩余阶段）",
}

skill_targets: dict[str, str] = {
    "self": "只能选自己",
    "other_alive": "只能选其他存活者",
    "any_alive": "存活者里任选（含自己）",
}


# ── 四、Agent 工具 ───────────────────────────────────────────

tools: dict[str, ToolSpec] = {
    "recall": ToolSpec("recall", ("long_memory",), "召回长期记忆"),
    "look": ToolSpec("look", (), "观察四周：场景/在场者/出口"),
    "check_self": ToolSpec("check_self", (), "查看自身状态"),
    # say 额外要求世界开了交流（comms.mode != none）：没有交流能力的世界不该有这个工具
    "say": ToolSpec("say", (), "对在场者发言（配额由代码计数）", requires_comms=True),
}


# ── 五、可写状态字段（delta 前缀 → 提供它的系统；None = 恒可写） ──

delta_fields: dict[str, str | None] = {
    "characters.*.hp": "combat",
    "characters.*.sanity": None,
    "characters.*.conditions": None,
    "characters.*.inventory": "equipment",
    "characters.*.position": "death",
    "characters.*.path": "identity",
    "characters.*.gold": None,
    "characters.*.supplies": None,
    "party.*": None,
    "clock.*": "time",
    "tension": None,
    "stage": None,
    "scene.location_id": "map",
    "scene.present_npcs": None,
    "scene.description": None,
    "quests": None,
    "quests.*": None,
    "enemies": "combat",
}


# ── 六、前端区块 + 世界包文件 ────────────────────────────────

ui_blocks: list[UiBlockSpec] = [
    UiBlockSpec("#hp-enemies", ("combat",), "敌人列表"),
    UiBlockSpec("#hp-target", ("combat",), "战斗目标选择"),
    UiBlockSpec("#hp-mode", ("combat",), "攻击/防御/逃跑"),
    UiBlockSpec("#hp-skill", ("combat",), "技能与数值"),
    UiBlockSpec("#chars", (), "队伍与角色栏（恒显示）"),
    UiBlockSpec("#profile-drawer", ("equipment",), "角色详情抽屉（背包/装备块）"),
    UiBlockSpec("#meta-char", ("meta_growth",), "局外成长：选角色"),
    UiBlockSpec("#meta-spend", ("meta_growth",), "局外成长：花点数"),
    UiBlockSpec("#promote-list", ("equipment", "meta_growth"), "物品晋升确认"),
    UiBlockSpec("#human-panel", ("human_seat",), "人类席位输入面板"),
    UiBlockSpec("#hmode", ("human_seat",), "托管模式开关"),
]

world_fields: list[WorldFieldSpec] = [
    WorldFieldSpec("world.yaml", True, None, "基调、组织、默认 era/region"),
    WorldFieldSpec("regions/", True, None, "地点表（每区一个 md）"),
    WorldFieldSpec("eras/", True, None, "技术水平与可用 tag"),
    WorldFieldSpec("npcs.yaml", True, None, "NPC 模板"),
    WorldFieldSpec("professions.yaml", True, None, "职业/身份牌"),
    WorldFieldSpec("seeds.yaml", True, None, "事件种子（内容门槛 C32）"),
    WorldFieldSpec("items.yaml", False, [], "装备模板与 starter"),
    WorldFieldSpec("meta_hooks.yaml", False, {}, "局外成长价目表"),
    WorldFieldSpec("origins.yaml", False, [], "出身（三层职业世界用）"),
    WorldFieldSpec("paths.yaml", False, [], "身份阶梯"),
    WorldFieldSpec("core/", False, "", "常驻世界观块"),
    WorldFieldSpec("archive/", False, [], "按需检索的资料"),
    WorldFieldSpec(
        "systems", False, {}, "能力开关声明；**缺省 = 已实现系统全部开启**"
    ),
    WorldFieldSpec(
        "turn_flow", False, {}, "阶段序列声明；**缺省 = 框架标准流程**"
    ),
    WorldFieldSpec("dice", False, {}, "骰型与判定形态；缺省用 rules/dice 默认"),
    WorldFieldSpec("memory", False, {}, "记忆策略；缺省用 config/memory.yaml"),
    WorldFieldSpec("prompts", False, {}, "prompt 骨架；缺省用 Python 内置骨架"),
    WorldFieldSpec("skills", False, [], "主动技能声明（`skills.yaml`）；空 = 无技能系统"),
]


# ── 便捷导出 ────────────────────────────────────────────────

ACTIVE_SYSTEMS = frozenset(sid for sid, spec in systems.items() if spec.status == "active")
PLANNED_SYSTEMS = frozenset(sid for sid, spec in systems.items() if spec.status == "planned")
PHASE_KINDS = frozenset(phase_kinds)
PREDICATE_NAMES = frozenset(predicates)
WIN_KINDS = frozenset(win_kinds)
SKILL_TRIGGERS = frozenset(skill_triggers)
SKILL_EFFECTS = frozenset(skill_effects)
SKILL_TARGETS = frozenset(skill_targets)
