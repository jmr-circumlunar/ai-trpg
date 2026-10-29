"""M12 Prompt · 玩家 Agent：系统提示、协议（`AGENT_PROTOCOL`）、行动请求。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import json
from pathlib import Path

from ...models import CharacterCard, GameState

from .blocks import *  # noqa: F401,F403

# 工具说明按「工具 → 片段」登记：关闭某个系统后，对应片段不再出现在 prompt 里（C51）。
# 新增工具 = 加 `_tool_<name>` + 登记 `_TOOLS` + 在 `manifest.tools` 声明依赖 + 这里加一段。
_TOOL_ORDER = ("recall", "look", "check_self", "say")

_TOOL_SNIPPETS: dict[str, str] = {
    "recall": (
        "- 检索自己的记忆（**默认不要用**；只在需要回忆 3 回合以前的事、确认旧道具、接续旧剧情线时才调）\n"
        '  {{"type": "tool", "tool": "recall", "args": {{"query": "钟楼 铁栅"}}}}\n'
    ),
    "look": (
        "- 观察场景细节\n"
        '  {{"type": "tool", "tool": "look", "args": {{"focus": "巷口"}}}}\n'
    ),
    "check_self": (
        "- 看自己的状态\n"
        '  {{"type": "tool", "tool": "check_self", "args": {{}}}}\n'
    ),
    "say": (
        "- 对在场者说一句话（试探、撒谎、结盟时用它；次数有限）\n"
        '  {{"type": "tool", "tool": "say", "args": {{"speech": "你昨晚去哪了？"}}}}\n'
    ),
}

_PROTOCOL_HEAD = """# 你可以调用工具，也可以直接提交行动
每次只输出一个 JSON 对象：

- 提交行动（**绝大多数情况直接用它**）
  {{"type": "final", "thought": "…", "action": "…", "approach": "…", "skill": "潜行", "target": null, "intent": "normal"}}

- 战斗回合（攻击 / 防御 / 逃跑）：多给一个 `mode`
  {{"type": "final", "thought": "…", "action": "扑上去压住它的手", "skill": "器械", "target": "en_1", "intent": "all_in", "mode": "attack"}}
  `mode` 只能是 `attack` / `defend` / `flee`；`target` 填场上敌人的 id；**非战斗回合不要带 mode**。
  逃跑成功就不再被这个敌人追打；防御只影响本回合受到的伤害。
"""

def _discipline(visible: tuple[str, ...]) -> str:
    """纪律段也要跟着可见工具走：工具不可见时，prompt 里不该再提它的名字。"""
    lines = ["", "# 纪律", "- 最多 3 步；正常情况**第一步就 final**。"]
    if "recall" in visible:
        lines.append("- 最近几回合发生的事你本来就记得（在上下文里），不需要 recall。")
    hints = [name for name in ("look", "recall") if name in visible]
    if hints:
        lines.append(f"- 想做一件事但缺信息时，才调 {' 或 '.join(hints)}。")
    return "\n".join(lines) + "\n"


def _world_extra(pack) -> str:
    """世界包对角色骨架的补充（B47）：`worlds/<id>/prompts/pc.md` 存在才生效。

    只做**追加**、不做替换：变量插值（姓名/秘密/工具/技能表）仍由框架负责。
    这样骨架加字段时世界包不会静默错位，也不会因为某个世界写了半份骨架就把别人写坏。
    """
    root = getattr(pack, "root", None)
    if root is None:
        return ""
    try:
        path = Path(root) / "prompts" / "pc.md"
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    return f"\n\n# 本世界的补充设定（prompts/pc.md）\n{text}\n"


def _comms_mode(pack) -> str:
    """世界包的交流档位；没有 pack 或没声明时是 `none`（= 没有交流能力）。"""
    if pack is None:
        return "none"
    try:
        declaration = pack.turn_flow_declaration() or {}
        return str((declaration.get("comms") or {}).get("mode") or "none")
    except Exception:  # noqa: BLE001
        return "none"


def visible_tools(pack) -> tuple[str, ...]:
    """本局可见的工具（C51）。

    **判据只有一份**：`rulespack.assemble.tool_set()`（依赖系统 + `requires_comms`）。
    这里只负责按 `_TOOL_ORDER` 排序——prompt 里工具顺序必须稳定，否则同样的能力
    每次生成不同前缀，prompt 缓存全废。
    """
    try:
        from ...rulespack import assemble as build_caps
        from ...rulespack import tool_set
    except Exception:  # noqa: BLE001
        return _TOOL_ORDER

    mode = _comms_mode(pack)
    caps = None
    if pack is not None:
        try:
            caps = build_caps(pack)
        except Exception:  # noqa: BLE001
            caps = None

    if caps is None:
        # 没有 pack（离线 / 单测）：只保留 comms 这一条门，其余视为全开
        return tuple(
            name for name in _TOOL_ORDER if not (name == "say" and mode == "none")
        )

    allowed = tool_set(caps, mode)
    return tuple(name for name in _TOOL_ORDER if name in allowed)


def agent_protocol(tools: tuple[str, ...] | list[str] | None = None) -> str:
    """拼协议：只列本局真正可用的工具。`None` = 全量（兼容老调用）。"""
    visible = _TOOL_ORDER if tools is None else tuple(tools)
    body = "".join(
        _TOOL_SNIPPETS[name] for name in _TOOL_ORDER if name in visible and name in _TOOL_SNIPPETS
    )
    return _PROTOCOL_HEAD + ("\n" + body if body else "") + _discipline(visible)


# 兼容：老代码/测试直接引用常量（= 全量协议）
AGENT_PROTOCOL = agent_protocol()


def pc_system(
    card: CharacterCard,
    skills: list[str],
    eras: str = "",
    pack=None,
    experience: list[str] | None = None,
) -> str:
    """角色 system prompt。

    `experience` 是**这个 agent 自己跨局积累的经验**（程序记忆，不走检索）。
    它属于角色段而不是共享段：每个 agent 的经验不同，放进共享段会破坏 prompt 缓存，
    更会让 B 的经验出现在 A 的 prompt 里。
    """
    ext = card.ext
    examples = (card.data.mes_example or "").strip()[:600]
    bits = []
    if ext.origin:
        bits.append(f"出身：{pack.origin_label(ext.origin) if pack is not None else ext.origin}")
    if ext.path:
        bits.append(f"身份：{pack.path_label(ext.path) if pack is not None else ext.path}")
    if ext.talents:
        bits.append(f"天赋：{'、'.join(ext.talents)}")
    identity_block = f"\n# 你的身份\n{'　'.join(bits)}\n" if bits else ""

    # 经验（程序记忆）：agent 跨局积累的判断。只放角色段，最多 8 条——
    # 再多就会把「这一局正在发生什么」挤掉。
    experience_block = ""
    learned = [str(item).strip() for item in (experience or []) if str(item).strip()]
    if learned:
        experience_block = (
            "\n# 你从过去的局里学到的（这是你的经验，不是这一局发生的事）\n"
            + "\n".join(f"- {line}" for line in learned[:8])
            + "\n"
        )
    # B32：**共享段在前、角色段在后**。前半段（规则 / 工具 / 世界补充 / 时代）对所有
    # 角色逐字相同，provider 的 prompt cache 才能命中；把姓名、秘密放前面会让每个
    # 角色的前缀都不同，缓存等于没用。
    return f"""# 规则
- 只输出 JSON，不要解释。
- thought 是私下盘算，别人看不到；action 只写你要做什么、怎么做，不写结果。
- skill 只能从这些里选：{"、".join(skills)}；不适用就填 null。
- 不得替其他角色行动，不得宣布自己成功。
- **你必须主动推进自己的目标**：整回合只做「观察」「等待」是浪费。没有更好的办法时，就制造一个机会。
- **thought 必须是中文**，且是角色的内心话（第一人称、口语、可带偏见），不是旁白也不是推理稿。≤ 40 字。
- 篇幅：thought ≤ 40 字，action ≤ 60 字，approach ≤ 20 字。**JSON 字符串内不要换行**（用空格代替），否则会被截断。

{agent_protocol(visible_tools(pack))}
{_world_extra(pack)}
# 你在扮演
{card.name}｜{card.data.personality}

# 背景
{card.data.description}
{identity_block}
# 说话方式（模仿例句的句式与语气）
{examples or "（无例句，保持口吻一贯）"}

# 你的秘密（对你之外的人不可见）
目标：{ext.private_goal}
恐惧：{ext.fear}
底线：{ext.bottom_line}
{experience_block}
{eras}"""

def pc_user(
    state: GameState,
    card: CharacterCard,
    public_recent: list[str],
    turn: int,
    memories: list[str] | None = None,
    chapter: list[str] | None = None,
    previous: list[str] | None = None,
    recent_turns: int = 3,
    pack=None,
    knowledge: str = "",
) -> str:
    ext = card.ext
    hp_max = ext.hp_max
    inv = "、".join(e.name_flavor or e.template_id for e in ext.equipment) or "空手"
    enemy_block = enemies_block(state)
    window = max(1, int(recent_turns or 3))
    recent = "\n".join(f"- {x}" for x in public_recent[-window:]) or "（故事刚开始）"
    chapter_block = (
        "\n".join(f"- {c}" for c in chapter) if chapter else "（还没有更早的回顾）"
    )
    # 短期记忆分层：近 3 回合原文常驻，更早靠摘要；具体长记忆要调 recall
    memory_block = ""
    if memories:
        memory_block = (
            "\n# 你记起的事（刚调过 recall）\n"
            "**这是你的记忆，不是档案：有的清楚，有的只是模糊印象，可能记错。**\n"
            "可以按它行动，但别把模糊印象当铁证。\n"
        ) + "\n".join(f"- {m}" for m in memories)
    previous_block = ""
    if previous:
        previous_block = "\n# 本回合已经发生（排在你前面的队友）\n" + "\n".join(
            f"- {x}" for x in previous
        )

    return f"""# 更早发生了什么（分章回顾）
{chapter_block}

# 最近几回合（你亲身经历，完全记得）
{recent}
{previous_block}{memory_block}

# 你的状态
HP {ext.hp}/{hp_max}　理智 {ext.sanity}/{ext.sanity_max}　装备：{inv}
身份：{identity_line(card, None, pack) or "（无）"}
当前地点：{state.scene.location_id}（{state.scene.region}）{enemy_block}
# 本回合
第 {turn} 回合，根据上面的局面决定你要做什么。

# 输出格式
{{"thought": "…", "action": "…", "approach": "…", "skill": "潜行", "target": null, "intent": "normal", "mode": null}}

`mode` 只在真的动手时填：`"attack"`（攻击，`target` 填敌人 id）/ `"defend"`（防御）/ `"flee"`（逃跑）；
非战斗回合填 `null`。**场上有敌人且你想打，就必须带 mode，否则代码不会结算攻击。**""" + (
        "\n\n" + knowledge if knowledge else ""
    )
