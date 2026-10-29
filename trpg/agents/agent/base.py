"""M12 Agent · 基座：常量 + 会话/模型调用管道。

`AgentLLM` 由三个 Mixin 组合而成（见 `agent/__init__.py`）：
- `AgentBase`    常量、`__init__`、`_spec`、`_chat_json`、token 记账
- `ToolMixin`    工具注册表 `_TOOLS` 与 `_tool_*` 实现
- `RuntimeMixin` GM 场景/叙事、玩家回合、NPC 行动、摘要

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import random
import time
from typing import Any

from ...models import CharacterCard, GameState, PlayerAction, StateDelta
from ...models.enums import CombatMode, Intent
from ...models.rng import derive
from ...memory.recall import hybrid_recall
from ...memory.settings import MemorySettings
from .. import prompts
from ..spec import LLMConfig, ModelSpec
from ..providers import extract_json

TASK_TOKENS = {"scene": 1200, "narrate": 2600, "player": 1200, "summary": 700}

# 技能表的真相在 rules/ruleset.yaml: skill_attr。
# 代码里**不再抄一份**（抄了就会漂移：换世界/改规则表时两处不一致）。
def _default_skills(rules=None) -> list[str]:
    """没有显式传技能表时，从规则里取；规则也没有就空（走通用检定）。"""
    if rules is None:
        return []
    try:
        return list(rules.skill_attr.keys())
    except Exception:  # noqa: BLE001
        return []

# 每多少回合压一条回顾、保留几条
# 摘要周期已外置到 config/memory.yaml（summary_every），代码里不再保留第二份真相
SUMMARY_KEEP = 4

DEFAULT_TABOOS = [
    "不涉及现实政治与真实人物",
    "不做任何性内容",
    "不写针对未成年人的暴力细节",
]


# 调用任务 → 分档角色。GM 写场景/后果用主力；NPC 单句与摘要是短活，走低档。
TASK_TIER = {
    "scene": "gm",
    "narrate": "gm",
    "ending": "ending",
    "player": "npc",
    "summary": "summary",
}


class AgentBase:
    def __init__(
        self,
        config: LLMConfig,
        cards: dict[str, CharacterCard],
        skills: list[str] | None = None,
        taboos: list[str] | None = None,
        style: str = "冷峻、克制，少形容词",
        world_name: str = "未命名世界",
        provider: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        rules=None,
        events=None,
        npcs: list[dict] | None = None,
        vectors=None,
        seeds: list[dict] | None = None,
        seed: str | int = 0,
        memory: MemorySettings | None = None,
        world_core: str = "",
        world_orgs: str = "",
        pack=None,
        legacy: str = "",
    ) -> None:
        self.config = config
        # 分档 spec 缓存（task -> spec），避免每次调用都重算
        self._spec_cache: dict[str, ModelSpec] = {}
        self.cards = cards
        self.skills = list(skills) if skills else _default_skills(rules)
        self.taboos = taboos or DEFAULT_TABOOS
        self.style = style
        self.world_name = world_name
        self.rules = rules
        self.events = events
        self.npcs = list(npcs or [])
        self.vectors = vectors
        self.recall_log: list[dict] = []  # 每次回忆的记录，供 trace 排查
        # 推理链（reasoning_content）：**只进 trace**，不上主页面。
        # 理由：推理里必然出现 DC / HP / 规则术语，印在叙事流里直接破坏沉浸。
        self.reasoning_log: list[dict] = []
        self._last_recall = None
        self.memory = memory or MemorySettings.load(getattr(config, "root", "."))
        self.world_core = world_core
        self.world_orgs = world_orgs
        self.pack = pack
        # 二期：上一局传闻（跨局传承，只进叙事不进判定）
        self.legacy = str(legacy or "")
        # 经验（**程序记忆**）：char_id → 该 agent 跨局积累的文本。
        # 由装配层在开局填（`Session.load_experience`），这里只负责被 `pc_system` 渲染。
        # 与 `_recent_public`（工作记忆）和 `memory_vectors`（情景记忆）分工不同：
        # 经验回答「我学到了什么」，少而精，**不走检索**。
        self.experience: dict[str, list[str]] = {}
        # char_id → agent_id：局末 `reflect` 要把经验写回**这个 agent**。
        # 没有映射就等于「经验只写不读」——这是接线前最大的空白。
        self.agent_ids: dict[str, str] = {}
        self.forced_seed: dict | None = None  # Director 指定的固定事件（优先于随机抽取）
        self.seeds = list(seeds or [])
        self.seed_rng = derive(seed, "seeds")
        self._used_seeds: set[str] = set()
        self.seed_log: list[dict] = []
        # P5：换幕要清空已用种子（否则抽完就再无剧情骨架）
        self._seed_stage: int = 0
        self._last_seed_id: str = ""
        self.default_spec: ModelSpec = config.resolve(
            provider=provider, model=model, reasoning_effort=reasoning_effort
        )
        # CLI 覆盖：临时实验开关，优先级高于角色卡
        self._override_provider = provider
        self._override_model = model
        self._override_effort = reasoning_effort

        self.usage: dict[str, Any] = {
            "calls": 0,
            "in": 0,
            "out": 0,
            "fail": 0,
            "retry": 0,
            "net_retry": 0,
        }
        # B27：每次真实模型调用一条记录（task / token / 耗时 / 重试），由编排层回合末 drain 落库
        self.call_log: list[dict] = []
        self._recent_public: list[str] = []
        # 分章回顾（每 `config/memory.yaml: summary_every` 回合压一条，留最近 chapter_keep 条）
        self._summaries: list[str] = []
        # B25：摘要落库回调（由编排层注入）；None = 纯内存（mock / 单测）
        self._summary_sink = None
        self._last_error: str = ""

    # ── 基础设施 ────────────────────────────────────────────
    @property
    def model(self) -> str:
        return f"{self.default_spec.provider}/{self.default_spec.model}"

    def _spec(self, task: str) -> ModelSpec:
        """按任务取分档 spec（`config/providers.yaml: tiers`），结果缓存。"""
        role = TASK_TIER.get(task, "gm")
        cached = self._spec_cache.get(role)
        if cached is None:
            try:
                cached = self.config.spec_for(role, self.default_spec)
            except Exception:  # noqa: BLE001  分档是优化，坏了就用默认
                cached = self.default_spec
            self._spec_cache[role] = cached
        return cached

    def _pc_spec(self, card) -> ModelSpec:
        """玩家 Agent 的 spec：显式配置 > 分档（`tiers.pc`，缺则回落 `tiers.npc`）> 全局默认。

        B23：PC 以前走独立 `resolve()`，返回的 spec 恒不等于 `default_spec`，
        `_chat_json` 的分档判断永远不命中——3 个 PC 因此成了最贵且最不受控的一段。
        角色卡/CLI 显式指定的 provider+model 仍然优先，分档只调思考深度与预算。
        """
        card_model = getattr(card.ext, "model", None) if card is not None else None
        base = self.config.resolve(
            card_model=card_model,
            provider=self._override_provider,
            model=self._override_model,
            reasoning_effort=self._override_effort,
        )
        try:
            tier = self.config.tier("pc") or self.config.tier("npc")
        except Exception:  # noqa: BLE001  分档是优化，坏了就用卡上的配置
            return base
        if not tier:
            return base

        explicit = bool(
            self._override_provider
            or self._override_model
            or (card_model is not None and card_model.model)
        )
        temp = tier.get("temperature")
        return ModelSpec(
            provider=base.provider if explicit else str(tier.get("provider") or base.provider),
            model=base.model if explicit else str(tier.get("model") or base.model),
            temperature=float(temp) if temp is not None else base.temperature,
            max_tokens=int(tier.get("max_tokens") or base.max_tokens),
            reasoning_effort=str(tier.get("reasoning_effort") or base.reasoning_effort),
        )

    def _count(
        self,
        result,
        task: str = "",
        model: str = "",
        elapsed_ms: int = 0,
        retry: int = 0,
    ) -> dict:
        """累计用量，并为这次调用留一条可落库的记录（B27）。"""
        usage = result.usage or {}
        tokens_in = int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        self.usage["calls"] += 1
        self.usage["in"] += tokens_in
        self.usage["out"] += tokens_out

        record = {
            "task": task,
            # agent = 角色（同一 task 恒定），task = 具体任务；两者分开才统计得出「谁贵」
            "agent": TASK_TIER.get(task, "gm"),
            "model": model or getattr(result, "model", "") or self.model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            # provider 自己计时更准；没填（mock / 自定义 adapter）时用调用侧的兜底值
            "latency_ms": int(getattr(result, "latency_ms", 0) or elapsed_ms or 0),
            "retry": int(getattr(result, "retry", 0) or 0) + int(retry or 0),
        }
        self.call_log.append(record)
        # 没被 drain 也没人跑回合时（如单测直连）不让它无限涨
        if len(self.call_log) > 500:
            self.call_log = self.call_log[-500:]
        return record

    def drain_calls(self) -> list[dict]:
        """取出自上次 drain 以来的模型调用记录并清空（B27 落库用）。"""
        records, self.call_log = self.call_log, []
        return records

    def set_summary_sink(self, sink) -> None:
        """注入摘要落库回调（B25），参数为 `(text, upto_turn)`。

        编排层有 db 连接时注入；mock / 单测没有连接，保持纯内存即可。
        """
        self._summary_sink = sink

    def hydrate(self, recent_public=None, summaries=None) -> None:
        """读档回灌（B25）：把落库的短期叙事与章节摘要塞回内存上下文。

        只保证顺序与类型，灌多少由调用方决定（M03 查多少给多少）；
        不传的维度保持现状，不覆盖。
        """
        if recent_public:
            self._recent_public = [str(x) for x in recent_public if str(x).strip()]
        if summaries:
            self._summaries = [str(x) for x in summaries if str(x).strip()]

    def _note_reasoning(self, result, task: str) -> None:
        """从原始响应里捞 `reasoning_content`（推理模型才有），留最近 20 条。"""
        try:
            msg = (result.raw.get("choices") or [{}])[0].get("message") or {}
            text = str(msg.get("reasoning_content") or "").strip()
        except Exception:  # noqa: BLE001
            return
        if not text:
            return
        self.reasoning_log.append({"task": task, "chars": len(text), "text": text[:2000]})
        self.reasoning_log = self.reasoning_log[-20:]

    def _chat_json(
        self, system: str, user: str, spec: ModelSpec, task: str, schema=None
    ) -> dict:
        """要一份 JSON。`schema` 给了且 adapter 支持结构化输出时，走 schema 约束。

        **为什么要两条路**：`MockLLM` 与自研 adapter 都没有 `structured()`，它们走原来的
        「自由文本 + `extract_json`」，行为与 golden 逐字节不变；只有装了 LangChain 传输层
        的真实模型才走 schema。结构化失败时**落回自由文本**，不把这一回合废掉。
        """
        # 只有「用会话级默认 spec」的调用（GM / NPC / 摘要 / 结局）才套分档；
        # 玩家 Agent 传的是自己卡上的 spec，不受分档影响。
        if spec is self.default_spec:
            spec = self._spec(task)
        max_tokens = spec.max_tokens or TASK_TOKENS.get(task, 1200)
        adapter = self.config.adapter(spec.provider)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # 思考深度按 spec 覆盖（角色卡或全局），空则不传
        override = (
            {"reasoning_effort": spec.reasoning_effort}
            if spec.reasoning_effort
            else None
        )

        label = f"{spec.provider}/{spec.model}"

        # 结构化输出（M2）：adapter 支持就用 schema 约束字段名与类型。
        # mock 与自研 adapter 没有 `structured()` → 跳过，走下面的自由文本路径（golden 不变）。
        structured = getattr(adapter, "structured", None)
        if schema is not None and callable(structured):
            started_at = time.perf_counter()
            try:
                parsed, latency_ms = structured(
                    schema,
                    messages,
                    spec.model,
                    spec.temperature,
                    max_tokens,
                    extra_override=override,
                )
                from ..providers.base import ChatResult

                # `with_structured_output` 不返回 token 用量，这里只记调用次数与耗时；
                # 成本对账以自研 adapter 的统计为准，两条链路并存时会有少量差异。
                self._count(
                    ChatResult(content="", model=spec.model, latency_ms=latency_ms),
                    task,
                    label,
                    latency_ms,
                )
                return (
                    parsed.model_dump()
                    if hasattr(parsed, "model_dump")
                    else dict(parsed)
                )
            except Exception as exc:  # noqa: BLE001
                # 结构化失败不废掉这一回合：记一条原因，落回自由文本路径
                self.usage["retry"] += 1
                self._last_error = f"structured[{label}]: {exc}"

        started = time.perf_counter()
        try:
            result = adapter.chat(
                messages, spec.model, spec.temperature, max_tokens, override
            )
            self._count(result, task, label, int((time.perf_counter() - started) * 1000))
            self._note_reasoning(result, task)
            return extract_json(result.content)
        except Exception as exc:
            self.usage["retry"] += 1
            retry_user = (
                f"{user}\n\n# 上一次输出不合要求\n{exc}\n请严格只输出合法 JSON，不要任何额外文字。"
            )
            started = time.perf_counter()
            result = adapter.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": retry_user}],
                spec.model,
                spec.temperature,
                max_tokens,
                override,
            )
            self._count(
                result, task, label, int((time.perf_counter() - started) * 1000), retry=1
            )
            self._note_reasoning(result, task)
            return extract_json(result.content)


__all__ = [
    "AgentBase",
    "DEFAULT_TABOOS",
    "SUMMARY_KEEP",
    "TASK_TIER",
    "TASK_TOKENS",
    "_default_skills",
]
